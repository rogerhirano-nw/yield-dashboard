"""Capture a REAL bid request Newsweek sends to Kargo, in a shareable form.

Why: when an SSP asks "send us a real bid request", what they need is the
exact OpenRTB payload their endpoint received — not a description of it and
not a synthetic one. Two different payloads can be meant by that phrase and
which one exists depends on how Kargo is wired in the wrapper:

  * CLIENT-SIDE (`hb_source=client`) — the browser itself POSTs OpenRTB to
    Kargo's endpoint (krk.kargo.com / krk2.kargo.com `/api/v1/openrtb`). That
    HTTP body IS the bid request, byte for byte, and this script captures it
    off the wire.
  * SERVER-SIDE (`hb_source=s2s`) — the browser only ever talks to Prebid
    Server; the Kargo-facing request is minted server-side and never exists
    in the page. The closest real artefact the publisher can produce is the
    PBS `/openrtb2/auction` request (which carries our imp/ext, GPID, floors,
    schain, eids and the `kargo` bidder params verbatim) plus pbjs's own
    `bidRequested` payload for kargo. This script captures those too and
    labels them for what they are, so nobody hands Kargo a PBS request while
    calling it a Kargo request.

It also records the matching bid RESPONSE and the pbjs `bidWon`/`noBid`
outcome, because "here is the request" is usually followed by "and here is
what you returned".

Usage:
    python scripts/capture_kargo_bid_request.py
    ARTICLE_URLS="https://www.newsweek.com/…-1234567" LOADS=4 python …
    PROFILES=mobile LOADS=10 python …          # Kargo is mobile-heavy demand

Env knobs: ARTICLE_URLS, LOADS, PROFILES (mobile,desktop), OUT_DIR,
CHROME_PATH, BROWSER_CHANNEL, BROWSER_PROXY, HEADFUL=1, INCOGNITO=1,
CONSENT (accept|decline), SCROLL_STEPS, SCROLL_DWELL, BIDDER (default kargo),
REDACT=1 (mask user identifiers before sharing — off by default, since the
whole point is usually to show Kargo the real identity signals).

Output in $OUT_DIR (default /tmp/kargo-bid-request):
    captures.json   every observed request/response, full fidelity
    sample.json     the single best client-side OpenRTB request, pretty
    summary.txt     human-readable digest for the email to Kargo

A residential IP and a real Chrome build get closer to what Kargo actually
sees in production; from a datacenter runner some demand paths stay quiet.
Run it from a laptop with:
    BROWSER_CHANNEL=chrome INCOGNITO=1 LOADS=8 python scripts/capture_kargo_bid_request.py
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlparse, parse_qs

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

HOME_URL = os.environ.get("HOME_URL") or "https://www.newsweek.com/"
ARTICLE_URLS = [u.strip() for u in (os.environ.get("ARTICLE_URLS") or "").split(",") if u.strip()]
LOADS = int(os.environ.get("LOADS") or "6")
PROFILES = [p.strip() for p in (os.environ.get("PROFILES") or "mobile,desktop").split(",") if p.strip()]
OUT = Path(os.environ.get("OUT_DIR") or "/tmp/kargo-bid-request")
BIDDER = (os.environ.get("BIDDER") or "kargo").lower()
CHROME_PATH = os.environ.get("CHROME_PATH") or ""
BROWSER_CHANNEL = os.environ.get("BROWSER_CHANNEL") or ""
HEADFUL = os.environ.get("HEADFUL") == "1"
INCOGNITO = os.environ.get("INCOGNITO") == "1"
CONSENT = (os.environ.get("CONSENT") or "accept").lower()
SCROLL_STEPS = int(os.environ.get("SCROLL_STEPS") or "14")
SCROLL_DWELL = float(os.environ.get("SCROLL_DWELL") or "0.8")
REDACT = os.environ.get("REDACT") == "1"

# Read the proxy fresh at launch: the agent proxy can move ports mid-session
# and a stale port fails every load with ERR_PROXY_CONNECTION_FAILED, which
# reads like the site refusing us rather than a stale socket.
def _current_proxy() -> str:
    return (os.environ.get("BROWSER_PROXY")
            or os.environ.get("HTTPS_PROXY")
            or os.environ.get("https_proxy") or "")

_ARTICLE_RE = re.compile(r"^https://www\.newsweek\.com/[a-z0-9-]+-\d{6,}$")

IPHONE_UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 "
             "(KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1")
DESKTOP_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36")
PROFILE_CFG = {
    "mobile": dict(viewport={"width": 390, "height": 844}, is_mobile=True,
                   has_touch=True, user_agent=IPHONE_UA),
    "desktop": dict(viewport={"width": 1440, "height": 900}, is_mobile=False,
                    has_touch=False, user_agent=DESKTOP_UA),
}

# Any host that is Kargo's, however the adapter is versioned. Older Prebid
# builds GET krk.kargo.com/api/v1/bid?json=…; current ones POST OpenRTB to
# krk2.kargo.com/api/v1/openrtb, and the pixel/sync hosts are kargo.com too.
_KARGO_HOST_RE = re.compile(r"(^|\.)kargo\.com$", re.I)
# Prebid Server auction endpoint — the s2s artefact.
_PBS_RE = re.compile(r"/openrtb2/auction", re.I)


_SYNC_RE = re.compile(r"/(dsync|usersync|cookie_?sync|setuid|pixel|sync)\b", re.I)


def _is_sync(url: str) -> bool:
    """Cookie-sync pixel, not a bid request. Prebid Server's /cookie_sync
    fires one per bidder configured on the ACCOUNT, independent of whether
    that bidder was called in this page's auction — so counting a sync as a
    bid request is exactly how you conclude "client-side" for a bidder that
    is actually server-side (or not on the page at all)."""
    return bool(_SYNC_RE.search(urlparse(url).path))


def _is_bidder_host(url: str) -> bool:
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return False
    if BIDDER == "kargo":
        return bool(_KARGO_HOST_RE.search(host))
    return BIDDER in host


# Installed before any page script. pbjs is queue-based, so pushing onto
# pbjs.que works whether or not the wrapper has booted yet; the events give
# us the wrapper's OWN view of the bid request (params, ortb2Imp, gpid),
# which is the artefact to send when the bidder is server-side.
INIT_JS = r"""
(() => {
  const B = "__BIDDER__";
  const out = { bidder: B, events: [], config: null, version: null, adUnits: null,
                // inventory: every bidder code the wrapper actually calls, with
                // its src (client|s2s) — this is what tells "kargo is s2s" from
                // "kargo is not on this page" when our bidder yields nothing.
                inventory: {}, allAdUnits: null, globals: [] };
  window.__kargoCap = out;
  const push = (type, payload) => {
    try { out.events.push({ t: Date.now(), type, payload: JSON.parse(JSON.stringify(payload)) }); }
    catch (e) { out.events.push({ t: Date.now(), type, payload: String(e) }); }
  };
  // Wrappers rename the global (owpbjs for PubMatic OpenWrap, custom names for
  // in-house builds). Hooking only `pbjs` silently captures nothing on those.
  const findPbjs = () => {
    const names = ["pbjs", "owpbjs", "pbjsNewsweek", "nwpbjs", "_pbjs"];
    for (const n of names) {
      const g = window[n];
      if (g && typeof g.onEvent === "function") { out.globals.push(n); return g; }
    }
    for (const k of Object.keys(window)) {
      if (!/pbjs/i.test(k)) continue;
      const g = window[k];
      if (g && typeof g.onEvent === "function") { out.globals.push(k); return g; }
    }
    return null;
  };
  const wire = () => {
    const pbjs = findPbjs();
    if (!pbjs) return false;
    try { out.version = pbjs.version; } catch (e) {}
    try {
      const c = pbjs.getConfig();
      // s2sConfig is the single fact that decides which artefact is real.
      out.config = { s2sConfig: c && c.s2sConfig, currency: c && c.currency,
                     floors: c && c.floors, userSync: c && c.userSync,
                     gvlMapping: c && c.gvlMapping };
    } catch (e) {}
    const keep = (bid) => bid && String(bid.bidder || "").toLowerCase() === B;
    pbjs.onEvent("bidRequested", (r) => {
      const code = String(r.bidderCode || "").toLowerCase();
      const src = String(r.src || (r.bids && r.bids[0] && r.bids[0].src) || "client");
      const key = code + "|" + src;
      out.inventory[key] = (out.inventory[key] || 0) + 1;
      if (code !== B) return;
      push("bidRequested", r);
    });
    pbjs.onEvent("bidResponse", (b) => { if (keep(b)) push("bidResponse", {
      adUnitCode: b.adUnitCode, cpm: b.cpm, currency: b.currency, size: b.size,
      mediaType: b.mediaType, dealId: b.dealId, creativeId: b.creativeId,
      timeToRespond: b.timeToRespond, meta: b.meta, source: b.source }); });
    pbjs.onEvent("noBid", (b) => { if (keep(b)) push("noBid", {
      adUnitCode: b.adUnitCode, bidder: b.bidder, params: b.params, src: b.src }); });
    pbjs.onEvent("bidTimeout", (bs) => (bs || []).forEach(
      (b) => { if (keep(b)) push("bidTimeout", { adUnitCode: b.adUnitCode }); }));
    pbjs.onEvent("bidWon", (b) => { if (keep(b)) push("bidWon", {
      adUnitCode: b.adUnitCode, cpm: b.cpm, size: b.size, dealId: b.dealId }); });
    pbjs.onEvent("auctionEnd", (a) => {
      try {
        // The ad units as configured, filtered to the ones that actually
        // carry this bidder — that is the publisher-side setup Kargo will
        // ask about (placement ids, sizes, GPID, floors).
        out.allAdUnits = (a.adUnits || []).map((u) => ({
          code: u.code, sizes: u.sizes,
          bidders: (u.bids || []).map((b) => String(b.bidder || "").toLowerCase()),
        }));
        out.adUnits = (a.adUnits || []).map((u) => ({
          code: u.code, mediaTypes: u.mediaTypes, ortb2Imp: u.ortb2Imp,
          bids: (u.bids || []).filter(keep),
        })).filter((u) => u.bids.length);
      } catch (e) {}
    });
    return true;
  };
  if (!wire()) {
    window.pbjs = window.pbjs || {};
    window.pbjs.que = window.pbjs.que || [];
    window.pbjs.que.push(wire);
    // Belt and braces: some wrappers replace pbjs wholesale after load.
    let n = 0;
    const iv = setInterval(() => { if (wire() || ++n > 60) clearInterval(iv); }, 500);
  }
})();
""".replace("__BIDDER__", BIDDER)


def _launch(pw):
    args = ["--no-sandbox", "--disable-dev-shm-usage",
            # The session's egress proxy re-terminates TLS and rejects
            # Chromium's TLS 1.3 ClientHello; capping keeps verification on.
            "--ssl-version-max=tls1.2"]
    if INCOGNITO:
        args.append("--incognito")
    kw = {"headless": not HEADFUL, "args": args}
    if BROWSER_CHANNEL:
        kw["channel"] = BROWSER_CHANNEL
    elif CHROME_PATH:
        kw["executable_path"] = CHROME_PATH
    proxy = _current_proxy()
    if proxy:
        kw["proxy"] = {"server": proxy}
    return pw.chromium.launch(**kw)


def _article_urls(browser, want: int) -> list[str]:
    if ARTICLE_URLS:
        return (ARTICLE_URLS * ((want // len(ARTICLE_URLS)) + 1))[:want]
    ctx = browser.new_context(**PROFILE_CFG["desktop"])
    pg = ctx.new_page()
    urls: list[str] = []
    try:
        pg.goto(HOME_URL, wait_until="domcontentloaded", timeout=60_000)
        time.sleep(4)
        hrefs = pg.eval_on_selector_all("a[href]", "els => els.map(e => e.href)")
        seen = set()
        for h in hrefs:
            if _ARTICLE_RE.match(h) and h not in seen:
                seen.add(h)
                urls.append(h)
    except Exception as exc:
        print(f"[warn] could not scrape article links: {exc}")
    finally:
        ctx.close()
    if not urls:
        raise SystemExit("no article URLs found — pass ARTICLE_URLS=…")
    print(f"[info] {len(urls)} article URLs scraped")
    return (urls * ((want // len(urls)) + 1))[:want]


def _decode_body(req) -> tuple[object | None, str | None]:
    """Return (parsed_json, raw_text). Kargo's older adapter passes the whole
    OpenRTB object as a `json=` query param on a GET, so a bid request with
    no POST body is normal, not a miss."""
    raw = None
    try:
        raw = req.post_data
    except Exception:
        raw = None
    if raw:
        try:
            return json.loads(raw), raw
        except Exception:
            return None, raw
    qs = parse_qs(urlparse(req.url).query)
    for key in ("json", "openrtb", "q"):
        if key in qs and qs[key]:
            try:
                return json.loads(qs[key][0]), qs[key][0]
            except Exception:
                return None, qs[key][0]
    return None, None


# Path-scoped, NOT key-name-scoped. A bare {"id"} rule looks right and is
# quietly destructive: it also masks imp.id, site.publisher.id and the request
# id, which are the fields the SSP needs to correlate the request with their
# own logs — you would hand Kargo an unusable sample and not notice.
_REDACT_PATHS = (
    ("user",),          # eids, buyeruid, consented identity
    ("device", "ip"),
    ("device", "ipv6"),
    ("device", "ifa"),
    ("source", "tid"),
)


# Inside an eid, only the uid VALUE is the identifier. Masking the whole
# object also hides `source` — which provider we pass (TDID, pubcid, ppuid) —
# and that is usually the exact thing the SSP is asking about.
_ID_VALUE_KEYS = {"id", "buyeruid", "ifa", "ip", "ipv6", "tid"}


def _mask(node):
    """Redact identifier VALUES only. Everything else — provider names, atype,
    stype, matcher — stays readable, because that structure is what the SSP is
    usually asking to see."""
    if isinstance(node, dict):
        out = {}
        for k, v in node.items():
            if k in _ID_VALUE_KEYS and isinstance(v, str):
                out[k] = "\u00abredacted\u00bb"
            elif isinstance(v, (dict, list)):
                out[k] = _mask(v)
            else:
                out[k] = v
        return out
    if isinstance(node, list):
        return [_mask(v) for v in node]
    return node


def _redact(obj):
    """Mask user identifiers. Off by default — Kargo already receives these
    fields in production, and stripping them hides the very signals a
    "why is our bid rate low" thread is usually about."""
    if not isinstance(obj, dict):
        return obj
    out = json.loads(json.dumps(obj))
    for path in _REDACT_PATHS:
        node = out
        for key in path[:-1]:
            node = node.get(key) if isinstance(node, dict) else None
            if node is None:
                break
        if isinstance(node, dict) and path[-1] in node:
            val = node[path[-1]]
            # A path can point at a scalar (source.tid) or a subtree (user);
            # _mask only rewrites keys it finds inside a container, so a bare
            # string at the end of an explicit path has to be set here.
            node[path[-1]] = "\u00abredacted\u00bb" if isinstance(val, str) else _mask(val)
    return out


def _run_one(browser, url: str, profile: str, idx: int) -> dict:
    ctx = browser.new_context(**PROFILE_CFG[profile])
    ctx.add_init_script(INIT_JS)
    page = ctx.new_page()
    hits: list[dict] = []
    pending: dict[str, dict] = {}

    def on_request(req):
        kind = None
        if _is_bidder_host(req.url):
            kind = "bidder_sync" if _is_sync(req.url) else "bidder_direct"
        elif _PBS_RE.search(req.url):
            kind = "prebid_server"
        if not kind:
            return
        parsed, raw = _decode_body(req)
        names_bidder = True
        if kind == "prebid_server":
            blob = (json.dumps(parsed) if parsed is not None else (raw or "")).lower()
            names_bidder = f'"{BIDDER}"' in blob
        rec = {"kind": kind, "names_bidder": names_bidder,
               "method": req.method, "url": req.url,
               "resource_type": req.resource_type,
               "headers": dict(req.headers), "body_json": parsed,
               "body_raw": None if parsed is not None else raw,
               "page_url": url, "profile": profile, "response": None}
        hits.append(rec)
        pending[id(req)] = rec
        pending[req.url + "|" + req.method] = rec

    def on_response(resp):
        rec = pending.get(resp.url + "|" + resp.request.method)
        if rec is None or rec.get("response") is not None:
            return
        try:
            body = resp.text()
        except Exception:
            body = None
        if body:
            try:
                body = json.loads(body)
            except Exception:
                body = body[:20000]
        rec["response"] = {"status": resp.status, "body": body}

    page.on("request", on_request)
    page.on("response", on_response)

    try:
        page.goto(url, wait_until="domcontentloaded", timeout=60_000)
    except Exception as exc:
        print(f"[warn] load failed {url}: {exc}")
        ctx.close()
        return {"url": url, "profile": profile, "hits": hits, "pbjs": None}

    if CONSENT == "accept":
        for sel in ('button:has-text("Accept")', 'button:has-text("I Accept")',
                    'button:has-text("Agree")', '#onetrust-accept-btn-handler',
                    'button[title="Accept"]'):
            try:
                el = page.query_selector(sel)
                if el and el.is_visible():
                    el.click(timeout=2000)
                    break
            except Exception:
                pass

    # Scroll: lazy in-article slots are where Kargo's demand mostly sits, so a
    # load that never scrolls captures only the top-of-page auction.
    step = PROFILE_CFG[profile]["viewport"]["height"] // 2
    for i in range(SCROLL_STEPS):
        try:
            page.evaluate(f"window.scrollBy(0, {step})")
        except Exception:
            break
        time.sleep(SCROLL_DWELL)
    time.sleep(2)

    try:
        cap = page.evaluate("window.__kargoCap || null")
    except Exception:
        cap = None
    ctx.close()
    n_direct = sum(1 for h in hits if h["kind"] == "bidder_direct" and (h["body_json"] or h["body_raw"]))
    n_pbs = sum(1 for h in hits if h["kind"] == "prebid_server")
    print(f"[{idx}] {profile} {url}\n     direct={n_direct} pbs={n_pbs} "
          f"pbjs_events={len(cap.get('events', [])) if cap else 0}")
    return {"url": url, "profile": profile, "hits": hits, "pbjs": cap}


def _pbs_bidders(hit: dict) -> set[str]:
    """Bidder codes a PBS auction actually called, read off its response.
    A server-side bidder is invisible in the request (the page sends one
    aliased imp and PBS expands it from the account config), so the response
    is the only place its participation shows up client-side."""
    body = (hit.get("response") or {}).get("body")
    if not isinstance(body, dict):
        return set()
    ext = body.get("ext") or {}
    names: set[str] = set()
    for key in ("responsetimemillis", "errors", "warnings"):
        val = ext.get(key)
        if isinstance(val, dict):
            names |= {str(k).lower() for k in val}
    for sb in body.get("seatbid") or []:
        if sb.get("seat"):
            names.add(str(sb["seat"]).lower())
    return names


def _pick_sample(pages: list[dict]) -> dict | None:
    """Best sample = a direct Kargo OpenRTB request with a parsed body and the
    most imps (a multi-imp auction shows Kargo the whole page context)."""
    best = None
    for pg in pages:
        for h in pg["hits"]:
            if h["kind"] != "bidder_direct" or not isinstance(h["body_json"], dict):  # syncs carry no bid
                continue
            imps = len(h["body_json"].get("imp") or [])
            score = (1, imps)
            if best is None or score > best[0]:
                best = (score, h)
    if best:
        return best[1]
    best_pbs = None
    for pg in pages:
        for h in pg["hits"]:
            if h["kind"] != "prebid_server" or not isinstance(h["body_json"], dict):
                continue
            reached = BIDDER in _pbs_bidders(h)
            imps = len(h["body_json"].get("imp") or [])
            score = (1 if reached else 0, imps)
            if best_pbs is None or score > best_pbs[0]:
                best_pbs = (score, h)
    return best_pbs[1] if best_pbs else None


def _summary(pages: list[dict], sample: dict | None) -> str:
    L: list[str] = []
    add = L.append
    add(f"Kargo bid-request capture — bidder={BIDDER}")
    add(f"pages loaded: {len(pages)}  profiles: {','.join(PROFILES)}")
    direct = [h for pg in pages for h in pg["hits"] if h["kind"] == "bidder_direct"]
    syncs = [h for pg in pages for h in pg["hits"] if h["kind"] == "bidder_sync"]
    pbs = [h for pg in pages for h in pg["hits"] if h["kind"] == "prebid_server"]
    pbs_named = [h for h in pbs if h.get("names_bidder")]
    add(f"direct {BIDDER} bid requests: {len(direct)}   cookie-sync pixels: {len(syncs)}")
    add(f"Prebid-Server auctions: {len(pbs)} (naming {BIDDER}: {len(pbs_named)})")
    ver = next((pg["pbjs"].get("version") for pg in pages if pg.get("pbjs")), None)
    add(f"pbjs version: {ver or 'unknown'}")
    s2s = next((pg["pbjs"].get("config", {}).get("s2sConfig")
                for pg in pages if pg.get("pbjs") and pg["pbjs"].get("config")), None)
    if s2s:
        cfgs = s2s if isinstance(s2s, list) else [s2s]
        for c in cfgs:
            bidders = [b.lower() for b in (c.get("bidders") or [])]
            add(f"  s2sConfig accountId={c.get('accountId')} endpoint={c.get('endpoint')} "
                f"bidders={len(bidders)} includes_{BIDDER}={BIDDER in bidders}")
    # Which PBS hosts reached this bidder, and via which client-side alias.
    host_hits: dict[str, int] = {}
    reached_hosts: dict[str, int] = {}
    for h in pbs:
        host = urlparse(h["url"]).hostname or "?"
        host_hits[host] = host_hits.get(host, 0) + 1
        if BIDDER in _pbs_bidders(h):
            reached_hosts[host] = reached_hosts.get(host, 0) + 1
    inv: dict[str, int] = {}
    for pg in pages:
        for k, v in ((pg.get("pbjs") or {}).get("inventory") or {}).items():
            inv[k] = inv.get(k, 0) + v
    mine = {k: v for k, v in inv.items() if k.split("|")[0] == BIDDER}
    add("")
    if direct:
        verdict = ("CLIENT-SIDE — the sample below is the literal HTTP body "
                   f"{BIDDER} received.")
    elif reached_hosts:
        hosts = ", ".join(f"{h} ({n} auctions)" for h, n in sorted(
            reached_hosts.items(), key=lambda kv: -kv[1]))
        aliases = sorted({k.split("|")[0] for k in inv if k.endswith("|s2s")})
        verdict = (
            f"SERVER-SIDE — {BIDDER} runs INSIDE Prebid Server at {hosts}. "
            f"The page calls it through the s2s alias {aliases or ['?']} and "
            f"never contacts {BIDDER} directly, so no browser-visible "
            f"{BIDDER} bid request exists. The captured sample is the "
            "client->PBS auction request, which carries every publisher-side "
            f"signal {BIDDER}'s copy is derived from (GPID, floors, site, "
            "device, user/eids, schain, regs). For the byte-exact outbound "
            "request, the PBS host is the only party that can export it.")
    elif pbs_named or mine:
        verdict = ("SERVER-SIDE — no browser-to-bidder request exists. Captured "
                   "instead is the Prebid Server auction request carrying our "
                   f"{BIDDER} imp/params; the {BIDDER}-facing request is minted "
                   "by PBS and never touches the page. Ask the PBS host "
                   "(Magnite/Rubicon) for the outbound copy if Kargo needs it.")
    elif syncs and not inv:
        verdict = (f"INCONCLUSIVE — only {BIDDER} cookie-sync pixels were seen and "
                   "no Prebid auction was observed at all (wrapper may not have "
                   "booted: consent, ad block, or a datacenter IP). Re-run from a "
                   "residential IP with BROWSER_CHANNEL=chrome.")
    elif syncs:
        verdict = (f"NOT REQUESTED — {BIDDER} was not in any observed auction on "
                   f"these pages, only its cookie-sync pixel fired (PBS syncs the "
                   "whole account bidder set regardless of the page). Either it is "
                   "not configured on this inventory or it is sampled/geo-gated.")
    else:
        verdict = f"NOT OBSERVED — no {BIDDER} traffic of any kind on these loads."
    add("WIRING VERDICT: " + verdict)
    add("")
    if host_hits:
        add("Prebid Server hosts seen (auctions / reached this bidder):")
        for h, n in sorted(host_hits.items(), key=lambda kv: -kv[1]):
            add(f"  {h}  {n} / {reached_hosts.get(h, 0)}")
        add("")
    if inv:
        add(f"bidders actually called ({len(inv)}), code|src = auctions:")
        for k, v in sorted(inv.items(), key=lambda kv: -kv[1]):
            add(f"  {k} = {v}")
    else:
        add("bidders actually called: NONE observed "
            f"(pbjs globals found: {[g for pg in pages for g in (pg.get('pbjs') or {}).get('globals', [])] or 'none'})")
    evs: dict[str, int] = {}
    for pg in pages:
        for e in (pg.get("pbjs") or {}).get("events", []):
            evs[e["type"]] = evs.get(e["type"], 0) + 1
    add("")
    add(f"pbjs events for {BIDDER}: " + (", ".join(f"{k}={v}" for k, v in sorted(evs.items())) or "none"))
    server_side: set[str] = set()
    for h in pbs:
        server_side |= _pbs_bidders(h)
    if server_side:
        add("")
        add(f"bidders PBS called server-side ({len(server_side)}): "
            + ", ".join(sorted(server_side)))
    au = next((pg["pbjs"].get("adUnits") for pg in pages
               if pg.get("pbjs") and pg["pbjs"].get("adUnits")), None)
    if au:
        add("")
        add(f"ad units carrying {BIDDER} ({len(au)}):")
        for u in au[:20]:
            gpid = ((u.get("ortb2Imp") or {}).get("ext") or {}).get("gpid")
            params = [b.get("params") for b in u.get("bids", [])]
            add(f"  {u.get('code')}  gpid={gpid}  params={json.dumps(params)}")
    if sample:
        add("")
        add(f"SAMPLE: {sample['kind']} {sample['method']} {sample['url']}")
        b = sample.get("body_json")
        if isinstance(b, dict):
            add(f"  id={b.get('id')}  imps={len(b.get('imp') or [])}  "
                f"tmax={b.get('tmax')}  cur={b.get('cur')}")
            site = b.get("site") or {}
            add(f"  site.page={site.get('page')}")
            add(f"  site.publisher={json.dumps(site.get('publisher'))}")
            for imp in (b.get("imp") or [])[:10]:
                ext = imp.get("ext") or {}
                add(f"  imp {imp.get('id')}: banner={bool(imp.get('banner'))} "
                    f"video={bool(imp.get('video'))} bidfloor={imp.get('bidfloor')} "
                    f"gpid={ext.get('gpid')} tagid={imp.get('tagid')}")
        resp = sample.get("response") or {}
        add(f"  response status={resp.get('status')}")
    return "\n".join(L)


def main() -> int:
    from playwright.sync_api import sync_playwright

    OUT.mkdir(parents=True, exist_ok=True)
    pages: list[dict] = []
    with sync_playwright() as pw:
        browser = _launch(pw)
        try:
            urls = _article_urls(browser, LOADS)
            for i, url in enumerate(urls, 1):
                profile = PROFILES[(i - 1) % len(PROFILES)]
                pages.append(_run_one(browser, url, profile, i))
                (OUT / "captures.json").write_text(json.dumps(pages, indent=2, default=str))
        finally:
            browser.close()

    sample = _pick_sample(pages)
    if sample:
        payload = sample.get("body_json")
        if payload is None:
            payload = {"_raw": sample.get("body_raw")}
        if REDACT:
            payload = _redact(payload)
        (OUT / "sample.json").write_text(json.dumps({
            "captured_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "kind": sample["kind"], "method": sample["method"], "url": sample["url"],
            "page_url": sample["page_url"], "profile": sample["profile"],
            "request_headers": sample["headers"],
            "request_body": payload,
            "response": sample.get("response"),
        }, indent=2, default=str))
    txt = _summary(pages, sample)
    (OUT / "summary.txt").write_text(txt)
    print("\n" + txt)
    print(f"\n[out] {OUT}/captures.json  {OUT}/sample.json  {OUT}/summary.txt")
    return 0 if sample else 1


if __name__ == "__main__":
    raise SystemExit(main())
