#!/usr/bin/env python3
"""Append the click-audience capture block to the Innovid creatives on the
Apple at Work PG interstitial (LI 7384069597) — publisher-side population of
first-party segment 9443004817 without touching the Innovid tag or asking
the agency. See docs/click_audience.md.

The appended <script> is self-contained (all errors swallowed, no
document.write, no %% sequences) and fires the DFPAudiencePixel activity tag
for the segment only on click-through-shaped signals:

  A) real click on an http(s) anchor inside the ad overlay      (exact)
  B) pagehide while the interstitial overlay is up              (same-tab nav)
  C) focus into the ad iframe + tab hidden within 1.6s          (new-tab nav)

A plain interstitial close produces none of these. Overlay detection is
anchored on ad iframes (innovid/doubleclick/googlesyndication src), so
paywall/consent overlays can't arm it.

Marker-idempotent: creatives already carrying `nw-click-audience` are
skipped. Dry-run by default; --apply writes; --rollback strips the block.
Creatives update ONE AT A TIME (SOAP constraint).
"""
import argparse
import os
import re
import sys
from pathlib import Path

envp = Path(__file__).resolve().parent.parent / ".env"
if envp.exists():
    for line in envp.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from gam_client import GAMClient
from googleads import ad_manager

V = "v202605"
LI_ID = 7384069597
SEG = "9443004817"
NET = "22541732127"
MARKER = "nw-click-audience"

CAPTURE_BLOCK = """
<!-- nw-click-audience:start seg=9443004817 -->
<script>
/* nw-click-audience v1 - adds users who CLICK THROUGH this creative to GAM
   first-party segment 9443004817 (yield-dashboard PR #351). Self-contained;
   the Innovid tag above is untouched. Close-without-click never fires. */
(function () {
  "use strict";
  var SEG = "9443004817", NET = "22541732127";
  var fired = false, blurArmedAt = 0, lastOverAt = 0, usp = null;

  var topWin = window, topDoc = document;
  try { void window.top.document; topWin = window.top; topDoc = window.top.document; } catch (e) {}

  try {
    topWin.__uspapi && topWin.__uspapi("getUSPData", 1, function (d, ok) {
      if (ok && d && d.uspString) { usp = d.uspString; }
    });
  } catch (e) {}

  function fire() {
    if (fired) { return; } fired = true;
    var u = "https://pubads.g.doubleclick.net/activity;dc_iu=/" + NET +
            "/DFPAudiencePixel;ord=" + Date.now() + ";dc_seg=" + SEG +
            (usp ? "?us_privacy=" + encodeURIComponent(usp) : "");
    try { fetch(u, { mode: "no-cors", credentials: "include", keepalive: true }); }
    catch (e) { try { (new Image()).src = u; } catch (e2) {} }
  }

  function isAdIframe(el) {
    try {
      if (!el || el.tagName !== "IFRAME") { return false; }
      if (el === window.frameElement) { return true; }
      return /innovid|doubleclick|googlesyndication/i.test(el.src || "");
    } catch (e) { return false; }
  }

  /* a candidate overlay = visible fixed/absolute element covering >=50% of
     the viewport that IS or CONTAINS an ad iframe (or our own frame) */
  function isCandidate(el) {
    try {
      var cs = topWin.getComputedStyle(el);
      if (!cs || cs.display === "none" || cs.visibility === "hidden" ||
          +cs.opacity === 0) { return false; }
      if (cs.position !== "fixed" && cs.position !== "absolute") { return false; }
      var vw = Math.max(topWin.innerWidth || 0, 1);
      var vh = Math.max(topWin.innerHeight || 0, 1);
      var r = el.getBoundingClientRect();
      if (r.width * r.height < 0.5 * vw * vh) { return false; }
      if (isAdIframe(el)) { return true; }
      var fr = el.querySelectorAll("iframe");
      for (var i = 0; i < fr.length; i++) {
        if (isAdIframe(fr[i])) { return true; }
      }
      return false;
    } catch (e) { return false; }
  }

  function overlayUp() {
    try {
      var els = topDoc.querySelectorAll("iframe,div");
      for (var i = 0; i < els.length; i++) {
        if (isCandidate(els[i])) { return true; }
      }
    } catch (e) {}
    return false;
  }

  function inCandidate(el) {
    try {
      while (el && el !== topDoc.documentElement) {
        if (isCandidate(el)) { return true; }
        el = el.parentElement;
      }
    } catch (e) {}
    return false;
  }

  function activeElInAd() {
    try {
      var ae = topDoc.activeElement;
      if (!ae || ae.tagName !== "IFRAME") { return false; }
      return isAdIframe(ae) || inCandidate(ae);
    } catch (e) { return false; }
  }

  try {
    /* A: real anchor click inside the overlay (or inside our own frame) */
    topDoc.addEventListener("click", function (e) {
      try {
        var t = e.target, a = t && t.closest && t.closest("a[href]");
        if (!a || !/^https?:/i.test(a.href || "")) { return; }
        if (inCandidate(a)) { fire(); }
      } catch (err) {}
    }, true);
    if (topDoc !== document) {
      document.addEventListener("click", function (e) {
        try {
          var t = e.target, a = t && t.closest && t.closest("a[href]");
          if (a && /^https?:/i.test(a.href || "")) { fire(); }
        } catch (err) {}
      }, true);
    }

    /* pointer memory for the blur path */
    topDoc.addEventListener("mouseover", function (e) {
      try {
        var t = e.target;
        if (isAdIframe(t) || inCandidate(t)) { lastOverAt = Date.now(); }
      } catch (err) {}
    }, true);

    /* C: focus drops into the ad iframe -> confirm with tab-hidden <=1.6s */
    topWin.addEventListener("blur", function () {
      try {
        if (activeElInAd() || Date.now() - lastOverAt < 800) {
          blurArmedAt = Date.now();
        }
      } catch (err) {}
    });
    topDoc.addEventListener("visibilitychange", function () {
      try {
        if (topDoc.visibilityState === "hidden" &&
            Date.now() - blurArmedAt <= 1600) { fire(); }
      } catch (err) {}
    });

    /* B: navigating away while the interstitial overlay is up */
    topWin.addEventListener("pagehide", function () {
      try { if (overlayUp()) { fire(); } } catch (err) {}
    });
  } catch (e) {}
})();
</script>
<!-- nw-click-audience:end -->"""

STRIP_RE = re.compile(
    r"\n?<!-- nw-click-audience:start.*?nw-click-audience:end -->",
    re.DOTALL)


def stmt(where, limit=50):
    return (ad_manager.StatementBuilder(version=V)
            .Where(where).Limit(limit).ToStatement())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--li", type=int, default=LI_ID)
    ap.add_argument("--creative-ids", default="",
                    help="comma-separated subset (default: all LICAs on the LI)")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--rollback", action="store_true",
                    help="strip the capture block instead of adding it")
    args = ap.parse_args()

    gc = GAMClient()
    client = gc._get_soap_client()
    c_svc = client.GetService("CreativeService", version=V)
    lica_svc = client.GetService("LineItemCreativeAssociationService", version=V)

    if args.creative_ids:
        ids = [int(x) for x in args.creative_ids.split(",") if x.strip()]
    else:
        licas = (lica_svc.getLineItemCreativeAssociationsByStatement(
            stmt(f"lineItemId = {args.li}")).results or [])
        ids = [int(x.creativeId) for x in licas]
    print(f"LI {args.li}: {len(ids)} creative(s): {ids}")
    if not ids:
        sys.exit("no creatives found")

    creatives = (c_svc.getCreativesByStatement(
        stmt(f"id IN ({', '.join(str(i) for i in ids)})")).results or [])

    mode = "ROLLBACK" if args.rollback else "ADD"
    print(f"mode: {mode}  apply: {args.apply}")
    if mode == "ADD":
        print(f"\nblock to append ({len(CAPTURE_BLOCK)} chars):"
              f"{CAPTURE_BLOCK[:220]}\n  ...\n")

    for c in creatives:
        snip = getattr(c, "snippet", None)
        if snip is None:
            print(f"- {c.id}: SKIP (no snippet field — {type(c).__name__})")
            continue
        has = MARKER in snip
        if mode == "ADD":
            if has:
                print(f"- {c.id}: SKIP (already has {MARKER})")
                continue
            new = snip + CAPTURE_BLOCK
        else:
            if not has:
                print(f"- {c.id}: SKIP (no {MARKER} block)")
                continue
            new = STRIP_RE.sub("", snip)
        print(f"- {c.id}: {len(snip)} -> {len(new)} chars"
              f"{'' if args.apply else '  (dry-run)'}")
        if args.apply:
            c.snippet = new
            upd = c_svc.updateCreatives([c])[0]  # one at a time (SOAP rule)
            tail = " ".join(str(upd.snippet).split())[-160:]
            print(f"    updated ok — snippet tail: ...{tail}")

    if not args.apply:
        print("\nDRY RUN — pass --apply to write.")


if __name__ == "__main__":
    main()
