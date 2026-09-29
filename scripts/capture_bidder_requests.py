#!/usr/bin/env python3
"""Record one Prebid bidder's real bid requests and responses on article pages.

Written 2026-09-29 for SmileWanted video. From a cloud/datacenter IP,
SmileWanted answers every request with 204 No Content, so all we could see
was our side: the video ad unit sends zoneId `newsweek.com_hb_display_46` (a
display zone) with `bidfloor: 0.3`. Run this from a residential connection
(a laptop at home or in the office) to see an actual bid body too.

It loads fresh article URLs scraped from the homepage (article pages are the
only inventory these bidders buy), scrolls each one so the in-article slots
and the video player run their auctions, and records:
  * every request to a host containing the bidder name, with the POST body
  * every response: status and body (204 means no bid)
  * the page's pbjs adUnits that include the bidder: mediaTypes + params

The request's `eids` (the user IDs of whoever runs this) are replaced with a
count before the file is written, since the output is meant to be shared.

Usage (from the repo root; the first line is a one-time setup):
    pip install playwright && python -m playwright install chromium
    python scripts/capture_bidder_requests.py
    python scripts/capture_bidder_requests.py --bidder smilewanted \\
        --articles 5 --device desktop --out /tmp/sw.json
    # keep going until SmileWanted bids on the video unit (~2.8% bid rate,
    # one video auction per article → expect 35-70 articles)
    python scripts/capture_bidder_requests.py --stop-on-bid video \\
        --articles 150 --tag video

Options: BROWSER_CHANNEL=chrome uses your installed Chrome instead of the
Playwright Chromium; BROWSER_PROXY routes through a proxy; HEADFUL=1 shows
the browser window.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

ARTICLE_RE = re.compile(r"newsweek\.com/[a-z0-9-]+-\d{6,}$")

ADUNITS_JS = """(bidder) => {
  const pb = window.pbjs;
  if (!pb || !pb.adUnits) return null;
  return pb.adUnits
    .filter(u => (u.bids || []).some(b => b.bidder === bidder))
    .map(u => ({code: u.code, mediaTypes: u.mediaTypes,
                params: u.bids.filter(b => b.bidder === bidder)
                              .map(b => b.params)}));
}"""


def _redact(body: str | None) -> object:
    if not body:
        return body
    try:
        data = json.loads(body)
    except ValueError:
        return body
    if isinstance(data, dict) and isinstance(data.get("eids"), list):
        data["eids"] = f"[{len(data['eids'])} user-ID entries redacted]"
    return data


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--bidder", default="smilewanted")
    ap.add_argument("--articles", type=int, default=3)
    ap.add_argument("--device", choices=["mobile", "desktop"], default="mobile")
    ap.add_argument("--out", default="bidder_capture.json")
    ap.add_argument("--stop-on-bid", metavar="TAG", default="",
                    help="keep loading articles (up to --articles) until the "
                         "bidder bids on this ad unit, e.g. video")
    ap.add_argument("--tag", default="",
                    help="only list this ad unit in the summary, e.g. video")
    args = ap.parse_args()
    needle = args.bidder.lower()

    caps: list[dict] = []
    with sync_playwright() as pw:
        kw: dict = {"headless": os.environ.get("HEADFUL") != "1",
                    "args": ["--no-sandbox", "--disable-dev-shm-usage"]}
        if os.environ.get("BROWSER_CHANNEL"):
            kw["channel"] = os.environ["BROWSER_CHANNEL"]
        if os.environ.get("BROWSER_PROXY"):
            kw["proxy"] = {"server": os.environ["BROWSER_PROXY"]}
            # Proxies that re-terminate TLS reject Chromium's TLS 1.3 hello.
            kw["args"].append("--ssl-version-max=tls1.2")
        browser = pw.chromium.launch(**kw)
        ctx_kw = (pw.devices["iPhone 13"] if args.device == "mobile"
                  else {"viewport": {"width": 1440, "height": 900}})
        ctx = browser.new_context(**ctx_kw)

        home = ctx.new_page()
        home.goto("https://www.newsweek.com/", wait_until="domcontentloaded",
                  timeout=60_000)
        urls: list[str] = []
        for h in home.eval_on_selector_all("a[href]", "as => as.map(a => a.href)"):
            if ARTICLE_RE.search(h) and h not in urls:
                urls.append(h)
        home.close()
        if not urls:
            raise SystemExit("no article links found on the homepage")

        def _bids_on(tag: str) -> list[dict]:
            return [c for c in caps if c["kind"] == "response"
                    and isinstance(c.get("request"), dict)
                    and c["request"].get("tagId") == tag
                    and isinstance(c["body"], dict) and c["body"].get("cpm")]

        done = 0
        while urls and done < args.articles:
            url = urls.pop(0)
            done += 1
            print(f"[{done}/{args.articles}] loading {url}")
            page = ctx.new_page()

            def on_request(r, url=url):
                if needle in r.url.lower():
                    caps.append({"kind": "request", "page": url, "url": r.url,
                                 "method": r.method, "body": _redact(r.post_data)})

            def on_response(r, url=url):
                if needle in r.url.lower():
                    try:
                        body = r.text()
                    except Exception as exc:  # redirects/aborted bodies
                        body = f"<unreadable: {exc}>"
                    caps.append({"kind": "response", "page": url, "url": r.url,
                                 "status": r.status, "body": _redact(body),
                                 "request": _redact(r.request.post_data)})

            page.on("request", on_request)
            page.on("response", on_response)
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=60_000)
                for _ in range(12):  # scroll so every lazy slot auctions
                    page.mouse.wheel(0, 600)
                    time.sleep(1.2)
                time.sleep(4)
                caps.append({"kind": "adunits", "page": url,
                             "config": page.evaluate(ADUNITS_JS, needle)})
                # Refill the queue from this article's own links, so a long
                # --stop-on-bid run doesn't run out of homepage links.
                if len(urls) < 20:
                    for h in page.eval_on_selector_all(
                            "a[href]", "as => as.map(a => a.href)"):
                        if ARTICLE_RE.search(h) and h not in urls and h != url:
                            urls.append(h)
            except Exception as exc:
                print(f"  failed: {exc}")
            page.close()
            # Write as we go, so stopping the run early keeps what it has.
            Path(args.out).write_text(json.dumps(caps, indent=1))
            if args.stop_on_bid:
                n = sum(1 for c in caps if c["kind"] == "request"
                        and isinstance(c["body"], dict)
                        and c["body"].get("tagId") == args.stop_on_bid)
                hit = _bids_on(args.stop_on_bid)
                print(f"  {args.stop_on_bid}: {n} requests, {len(hit)} bids so far")
                if hit:
                    pair = {"request": hit[0]["request"], "response": hit[0]["body"],
                            "status": hit[0]["status"], "page": hit[0]["page"]}
                    first = Path(args.out).with_name(
                        f"{needle}_{args.stop_on_bid}_bid.json")
                    first.write_text(json.dumps(pair, indent=1))
                    print(f"  → {args.stop_on_bid} bid captured: {first}")
                    break
        browser.close()

    Path(args.out).write_text(json.dumps(caps, indent=1))

    # Summary: one line per bid response, keyed on the request it answers.
    pairs = [c for c in caps if c["kind"] == "response"
             and isinstance(c.get("request"), dict)
             and (not args.tag or c["request"].get("tagId") == args.tag)]
    n_req = sum(c["kind"] == "request" for c in caps)
    print(f"\n{n_req} requests, {len(pairs)} bid responses → {args.out}")
    for c in pairs:
        b = c["request"]
        detail = ""
        if isinstance(c["body"], dict):
            rb = c["body"]
            detail = (f"  BID cpm={rb.get('cpm')} {rb.get('width')}x{rb.get('height')}"
                      f" format={rb.get('formatTypeSw')}")
        print(f"  {str(b.get('tagId')):<20}{str(b.get('zoneId')):<30}"
              f"floor={b.get('bidfloor')}  ctx={b.get('context') or '-'}"
              f"  → {c['status']}{detail}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
