#!/usr/bin/env python3
"""Move the click-audience capture from per-creative snippet appends to a GAM
Creative Wrapper — the native mechanism for serving an HTML snippet alongside
every creative in an ad unit (survives agency creative swaps; snippets stay
byte-identical; one-switch kill).

Per the v202605 schema, creative wrappers attach to a LabelType CREATIVE_WRAPPER
label applied to AD UNITS (AdUnit.appliedLabels) — unit-scoped, not LI-scoped.
The wrapper therefore injects into everything serving in newsweek/interstitial,
so the block v2w carries a hard LI GATE: it resolves the serving line item via
googletag response info (slot whose container contains our frameElement) and
returns inert unless the LI is allow-listed (7384069597). GAM preview responses
carry no lineItemId, so a preview URL naming lineItemId=7384069597 also passes
(verification path).

Idempotent at every step (label by name, wrapper by labelId, applied-label by
id). Dry-run default; --apply to write. The AdUnit label application may be
PERMISSION_DENIED for this SA (it cannot CREATE units; update is untested) —
on denial the script prints the 30-second UI step instead of failing.

After the wrapper is verified serving, strip the per-creative blocks via
add_click_capture workflow target=rollback so the code lives in exactly one
place. See docs/click_audience.md.
"""
import argparse
import os
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
LABEL_NAME = "[nw] click-audience wrapper - Apple at Work clickers"
UNIT_CODE = "interstitial"

WRAPPER_FOOTER = """
<!-- nw-click-audience:start seg=9443004817 via=wrapper -->
<script>
/* nw-click-audience v4w (creative wrapper, LI-gated) - adds users who CLICK
   THROUGH the Apple at Work interstitial (LI 7384069597) to GAM first-party
   segment 9443004817. Injected by creative wrapper on the interstitial unit;
   INERT for every other line item serving there. v3: fire in the exact
   pcd.js request form (dc_seg before ord + ?ptt=22) + official pcd.js
   injection as belt-and-braces; LI gate accepts the Apple Innovid loader
   signature (r1.6a68d35b*), immune to null GPT response info on programmatic
   serves. v4 (2026-08-07): liberalized C-signal for the low-click-volume
   era - arming window 800ms->2500ms, blur->hidden fire window 1.6s->3.5s,
   pointerdown/touchstart inside the overlay also arms (touch/hybrid).
   Known cost: close-then-tab-switch within ~3.5s can over-count (was ~1.6s).
   yield-dashboard PR #351. */
(function () {
  "use strict";
  var SEG = "9443004817", NET = "22541732127";
  var LI_ALLOW = { "7384069597": 1 };
  var fired = false, blurArmedAt = 0, lastOverAt = 0, usp = null;

  var topWin = window, topDoc = document;
  try { void window.top.document; topWin = window.top; topDoc = window.top.document; } catch (e) {}

  function servingLiOk() {
    try {
      if ((topWin.location.search || "").indexOf("lineItemId=7384069597") > -1) { return true; }
      try {
        /* the wrapped creative IS one of the three Apple Innovid tags */
        var h = document.documentElement.innerHTML || "";
        if (h.indexOf("rtr.innovid.com/js/r1.6a68d35b") > -1) { return true; }
      } catch (e) {}
      var gt = topWin.googletag;
      if (!gt || !gt.pubads) { return false; }
      var fe = window.frameElement, slots = gt.pubads().getSlots();
      for (var i = 0; i < slots.length; i++) {
        var el = topDoc.getElementById(slots[i].getSlotElementId());
        if (el && fe && el.contains(fe)) {
          var ri = slots[i].getResponseInformation && slots[i].getResponseInformation();
          if (!ri) { return false; }
          var id = ri.lineItemId != null ? ri.lineItemId : ri.sourceAgnosticLineItemId;
          return !!LI_ALLOW[String(id)];
        }
      }
    } catch (e) {}
    return false;
  }
  if (!servingLiOk()) { return; }

  try {
    topWin.__uspapi && topWin.__uspapi("getUSPData", 1, function (d, ok) {
      if (ok && d && d.uspString) { usp = d.uspString; }
    });
  } catch (e) {}

  function fire() {
    if (fired) { return; } fired = true;
    /* exact pcd.js form: dc_seg;ord path order + ?ptt=22 */
    var u = "https://pubads.g.doubleclick.net/activity;dc_iu=/" + NET +
            "/DFPAudiencePixel;dc_seg=" + SEG + ";ord=" +
            Math.floor(Math.random() * 9e15) + "?ptt=22" +
            (usp ? "&us_privacy=" + encodeURIComponent(usp) : "");
    try {
      fetch(u, { mode: "no-cors", credentials: "include", keepalive: true })
        .catch(function () { try { (new Image()).src = u; } catch (e) {} });
    } catch (e) { try { (new Image()).src = u; } catch (e2) {} }
    /* belt-and-braces: let Google's own pcd.js issue the canonical request
       when the document survives (new-tab click-throughs - the unit's
       Target window is _blank, so this is the common path) */
    try {
      setTimeout(function () {
        try {
          if (document.getElementById("google-pcd-tag")) { return; }
          var s = document.createElement("script");
          s.async = true;
          s.id = "google-pcd-tag";
          s.src = "https://pagead2.googlesyndication.com/pagead/js/pcd.js";
          s.setAttribute("data-audience-pixel",
            "dc_iu=/" + NET + "/DFPAudiencePixel;dc_seg=" + SEG);
          (document.head || document.documentElement).appendChild(s);
        } catch (e) {}
      }, 0);
    } catch (e) {}
  }

  function isAdIframe(el) {
    try {
      if (!el || el.tagName !== "IFRAME") { return false; }
      if (el === window.frameElement) { return true; }
      return /innovid|doubleclick|googlesyndication/i.test(el.src || "");
    } catch (e) { return false; }
  }

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

    topDoc.addEventListener("mouseover", function (e) {
      try {
        var t = e.target;
        if (isAdIframe(t) || inCandidate(t)) { lastOverAt = Date.now(); }
      } catch (err) {}
    }, true);
    topDoc.addEventListener("pointerdown", function (e) {
      try {
        var t = e.target;
        if (isAdIframe(t) || inCandidate(t)) { lastOverAt = Date.now(); }
      } catch (err) {}
    }, true);
    topDoc.addEventListener("touchstart", function (e) {
      try {
        var t = e.target;
        if (isAdIframe(t) || inCandidate(t)) { lastOverAt = Date.now(); }
      } catch (err) {}
    }, true);

    topWin.addEventListener("blur", function () {
      try {
        if (activeElInAd() || Date.now() - lastOverAt < 2500) {
          blurArmedAt = Date.now();
        }
      } catch (err) {}
    });
    topDoc.addEventListener("visibilitychange", function () {
      try {
        if (topDoc.visibilityState === "hidden" &&
            Date.now() - blurArmedAt <= 3500) { fire(); }
      } catch (err) {}
    });

    topWin.addEventListener("pagehide", function () {
      try { if (overlayUp()) { fire(); } } catch (err) {}
    });
  } catch (e) {}
})();
</script>
<!-- nw-click-audience:end -->"""


def stmt(where, limit=10):
    return (ad_manager.StatementBuilder(version=V)
            .Where(where).Limit(limit).ToStatement())


KNOWN_WRAPPER_ID = 391280066   # bound to label 391280066, applied to the unit
DUPLICATE_WRAPPER_ID = 391439379  # accidental 8/6 pair (label applied nowhere)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    gc = GAMClient()
    client = gc._get_soap_client()
    lbl_svc = client.GetService("LabelService", version=V)
    cw_svc = client.GetService("CreativeWrapperService", version=V)
    inv_svc = client.GetService("InventoryService", version=V)

    # Diagnose the 8/6 name-lookup miss: dump stored label names in the family.
    try:
        fam = lbl_svc.getLabelsByStatement(
            stmt("name LIKE '%click-audience%'", 10)).results or []
        for L in fam:
            print(f"label on file: id={L.id} active={getattr(L, 'isActive', None)} "
                  f"name={L.name!r}")
    except Exception as e:
        print("label family dump failed:", repr(e)[:150])

    # Fast path: the LIVE wrapper (label applied to the unit by Roger) is known
    # by id — update its footer in place; never mint new label/wrapper pairs.
    wraps = cw_svc.getCreativeWrappersByStatement(
        stmt(f"id = {KNOWN_WRAPPER_ID}", 1)).results or []
    if wraps:
        w = wraps[0]
        cur = getattr(w, "htmlFooter", "") or ""
        ver = ("v4w" if "nw-click-audience v4w" in cur
               else "v3w" if "ptt=22" in cur else "v2w/older")
        print(f"LIVE wrapper {w.id}: status={getattr(w, 'status', None)} "
              f"labelId={getattr(w, 'labelId', None)} footer={len(cur)} chars "
              f"ver={ver}")
        if cur != WRAPPER_FOOTER:
            if args.apply:
                w.htmlFooter = WRAPPER_FOOTER
                upd = cw_svc.updateCreativeWrappers([w])[0]
                print(f"LIVE wrapper footer UPDATED -> {len(upd.htmlFooter)} "
                      f"chars (v3={'ptt=22' in upd.htmlFooter})")
            else:
                print("LIVE wrapper footer differs — would update (dry-run)")
        else:
            print("LIVE wrapper footer already v3 — nothing to do")
        # retire the accidental duplicate pair
        try:
            dup = cw_svc.getCreativeWrappersByStatement(
                stmt(f"id = {DUPLICATE_WRAPPER_ID}", 1)).results or []
            if dup and str(getattr(dup[0], "status", "")) == "ACTIVE":
                if args.apply:
                    r = cw_svc.performCreativeWrapperAction(
                        {"xsi_type": "DeactivateCreativeWrappers"},
                        stmt(f"id = {DUPLICATE_WRAPPER_ID}", 1))
                    print(f"duplicate wrapper {DUPLICATE_WRAPPER_ID} "
                          f"deactivated ({getattr(r, 'numChanges', '?')} change)")
                else:
                    print(f"duplicate wrapper {DUPLICATE_WRAPPER_ID} ACTIVE — "
                          f"would deactivate")
        except Exception as e:
            print("duplicate cleanup failed:", repr(e)[:200])
        return

    units = inv_svc.getAdUnitsByStatement(
        stmt(f"adUnitCode = '{UNIT_CODE}'", 5)).results or []
    if not units:
        sys.exit(f"FATAL: ad unit with code '{UNIT_CODE}' not found")
    unit = units[0]
    applied = list(getattr(unit, "appliedLabels", None) or [])
    print(f"ad unit: id={unit.id} code={unit.adUnitCode!r} "
          f"status={getattr(unit, 'status', None)}")
    print(f"  existing appliedLabels: {[int(a.labelId) for a in applied]}")

    sb = (ad_manager.StatementBuilder(version=V)
          .Where("name = :n").WithBindVariable("n", LABEL_NAME).Limit(1))
    found = lbl_svc.getLabelsByStatement(sb.ToStatement()).results or []
    if found:
        label_id = int(found[0].id)
        print(f"label exists: id={label_id}")
    elif args.apply:
        lab = lbl_svc.createLabels([{
            "name": LABEL_NAME,
            "description": ("Creative wrapper carrier for the click-audience "
                            "capture (segment 9443004817). PR #351."),
            "types": ["CREATIVE_WRAPPER"],
        }])[0]
        label_id = int(lab.id)
        print(f"label created: id={label_id}")
    else:
        label_id = None
        print(f"label would be created: {LABEL_NAME!r}")

    if label_id is not None:
        wraps = cw_svc.getCreativeWrappersByStatement(
            stmt(f"labelId = {label_id}", 5)).results or []
        if wraps:
            w = wraps[0]
            cur = getattr(w, "htmlFooter", "") or ""
            print(f"wrapper exists: id={w.id} status={getattr(w, 'status', None)} "
                  f"footer={len(cur)} chars")
            if cur != WRAPPER_FOOTER:
                if args.apply:
                    w.htmlFooter = WRAPPER_FOOTER
                    upd = cw_svc.updateCreativeWrappers([w])[0]
                    print(f"wrapper footer UPDATED: {len(cur)} -> "
                          f"{len(upd.htmlFooter)} chars "
                          f"(v3w pcd-form fire + hardened gate)")
                else:
                    print(f"wrapper footer DIFFERS ({len(cur)} vs "
                          f"{len(WRAPPER_FOOTER)} chars) — would update")
        elif args.apply:
            w = cw_svc.createCreativeWrappers([{
                "labelId": label_id,
                "creativeWrapperType": "HTML",
                "htmlFooter": WRAPPER_FOOTER,
                "ordering": "NO_PREFERENCE",
            }])[0]
            print(f"wrapper created: id={w.id} status={getattr(w, 'status', None)}")
        else:
            print(f"wrapper would be created: footer {len(WRAPPER_FOOTER)} chars")
    else:
        print(f"wrapper would be created after label ({len(WRAPPER_FOOTER)} chars)")

    if label_id is not None and any(int(a.labelId) == label_id for a in applied):
        print("label already applied to the ad unit — nothing to do")
        return
    if not args.apply:
        print(f"\nDRY RUN — would apply label to ad unit {unit.id}; "
              "pass --apply to write.")
        return
    try:
        unit.appliedLabels = applied + [{"labelId": label_id, "isNegated": False}]
        upd = inv_svc.updateAdUnits([unit])[0]
        print(f"label applied to ad unit {upd.id}: "
              f"{[int(a.labelId) for a in (upd.appliedLabels or [])]}")
    except Exception as e:
        print(f"\nAdUnit label application FAILED ({repr(e)[:200]})")
        print("Manual UI step (30s): GAM > Inventory > Ad units > "
              f"'{UNIT_CODE}' > Settings > Labels > add {LABEL_NAME!r} > Save.")


if __name__ == "__main__":
    main()
