"""Create the GAM side of the article-page sponsor logo, served through the
existing out-of-page unit `oop2` (/22541732127/newsweek/oop2) — everything
through GAM, no newsweek.com change of any kind.

One sponsorship line item targeting BOTH oop2 and inarticle1, with two
creatives (sizes route each request to the right one):

  1. Out-of-page CustomCreative on the oop2 request (SafeFrame OFF) — the
     product. Plants a parent-document watcher that injects a
     "Presented by <logo>" strip at the right of the article breadcrumb row
     (Autos | Volvo | Safety ............ Presented by [logo]) and keeps it
     there through React re-renders. Renders nothing off article pages.
  2. 300x250 bootstrap CustomCreative on the inarticle1 request — invisible
     plumbing. The article templates define + fetch oop2 but (since the
     Next.js migration) never bind its div, so GPT's display fails and the
     oop2 creative would never execute. The bootstrap collapses its own
     slot, binds the missing `dfp-ad-oop2` div, and re-triggers the render
     — after which the oop2 creative serves normally with all impressions/
     clicks counted on this line item via oop2. Cost: the sponsorship
     occupies (and hides) the first in-article position on covered pages.

The [TEST] creatives use an inline SVG placeholder logo. Real sponsor
flights: clone the LI onto the sales order and swap the logo/click URL —
see docs/article_sponsor_logo.md.

Lookup-first: every step skips objects that already exist (by name), so the
script is safe to re-run after a partial apply.

Usage:
    python3 scripts/setup_article_sponsor_logo.py           # dry run
    python3 scripts/setup_article_sponsor_logo.py --apply   # create in GAM
"""

import json
import os
import sys
import tempfile
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")

DRY_RUN = "--apply" not in sys.argv

# ── .env ──────────────────────────────────────────────────────────────────────
_env = Path(__file__).resolve().parent.parent / ".env"
if _env.exists():
    with open(_env) as _f:
        for _line in _f:
            _line = _line.strip()
            if not _line or _line.startswith("#") or "=" not in _line:
                continue
            _k, _v = _line.split("=", 1)
            _v = _v.strip().strip('"')
            os.environ.setdefault(_k.strip(), _v)

from googleads import ad_manager, oauth2  # type: ignore

_sa = json.loads(os.environ["GAM_SERVICE_ACCOUNT_JSON"])
with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as _f:
    json.dump(_sa, _f)
    _kf = _f.name
_oc = oauth2.GoogleServiceAccountClient(_kf, "https://www.googleapis.com/auth/dfp")
client = ad_manager.AdManagerClient(
    _oc, "NewsweekDashboard/1.0", network_code=os.environ["GAM_NETWORK_ID"]
)
V = "v202605"

# ── config ────────────────────────────────────────────────────────────────────
OOP2_AD_UNIT_ID      = 23207098418   # /22541732127/newsweek/oop2 (out-of-page)
INARTICLE1_AD_UNIT_ID = 23206070574  # /22541732127/newsweek/inarticle1 (bootstrap carrier)

ORDER_ID      = 4082002976           # Newsweek_Test-2 (same as newsletter tests)
ADVERTISER_ID = 5131205161
LI_NAME       = "[TEST] Article Sponsor Logo - oop2"
CR_NAME       = "[TEST] Article Sponsor Logo - oop2"
BOOT_CR_NAME  = "[TEST] Article Sponsor Logo - oop2 bootstrap (300x250)"
CLICK_URL     = "https://www.newsweek.com"

SPONSOR_LABEL = "Presented by"       # disclosure label rendered next to the logo

# [TEST] placeholder logo — grey "SPONSOR" box. Real flights: swap for the
# sponsor's hosted logo URL (or a CustomCreativeAsset + %%FILE:...%% macro).
LOGO_SRC = (
    "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='120' "
    "height='24'%3E%3Crect width='120' height='24' rx='3' fill='%23d8d2c4'/%3E"
    "%3Ctext x='60' y='16' font-family='sans-serif' font-size='11' "
    "fill='%2368645a' text-anchor='middle'%3ESPONSOR%3C/text%3E%3C/svg%3E"
)

# ── creative snippet ──────────────────────────────────────────────────────────
# oop2 renders in a friendly (same-origin) iframe as long as the creative is
# NOT SafeFrame-compatible, so the JS can reach the parent document. The
# snippet plants a watcher <script> INTO the parent document, which then
# injects the strip at the right of the breadcrumb row. Two reasons for the
# indirection (learned 2026-06-11, see docs/article_sponsor_logo.md):
#   - Next.js hydration destroys the oop divs (and this creative's iframe)
#     shortly after GPT renders them; a watcher living in the parent
#     document survives that and still injects.
#   - Hydration re-renders can remove an already-injected strip; the
#     watcher re-injects for ~2 minutes, then stops.
# It bails silently on pages without a breadcrumb row (self-scoping).
# Selectors match the hashed CSS-module classes by their stable prefix/suffix.
_INJECT_CSS = (
    "#nw-sponsor-logo{position:absolute;right:0;top:50%;transform:translateY(-50%);"
    "display:flex;align-items:center;gap:10px;text-decoration:none}"
    "#nw-sponsor-logo .sl-label{font-family:system-ui,-apple-system,sans-serif;"
    "font-size:11px;font-weight:500;letter-spacing:.08em;color:#68645a;"
    "text-transform:uppercase;white-space:nowrap}"
    "#nw-sponsor-logo .sl-logo{display:block;height:24px;width:auto;"
    "max-width:200px;object-fit:contain}"
    "@media(max-width:767px){#nw-sponsor-logo{position:static;transform:none;"
    "width:100%;justify-content:flex-end;margin-top:8px}}"
)

_SNIPPET = """\
<script>
(function () {
  try {
    var W = window.top, D = W.document;
    if (D.getElementById('nw-sponsor-logo-boot')) return;
    var CFG = {
      label: '__LABEL__',
      logo: '__LOGO__',
      href: '%%CLICK_URL_UNESC%%%%DEST_URL%%',
      css: __CSS__
    };
    /* Runs in the PARENT document so hydration destroying this creative
       iframe cannot kill it. Re-injects the strip whenever a re-render
       removes it; self-stops after ~2 minutes. */
    function watcher(cfg) {
      var d = document;
      function ensure() {
        try {
          if (d.getElementById('nw-sponsor-logo')) return;
          var bc = d.querySelector('[class*="ResponsiveBreadcrumbs"][class*="__container"]');
          if (!bc) return;  /* not an article page: render nothing */
          if (!d.getElementById('nw-sponsor-logo-css')) {
            var st = d.createElement('style');
            st.id = 'nw-sponsor-logo-css';
            st.textContent = cfg.css;
            d.head.appendChild(st);
          }
          var a = d.createElement('a');
          a.id = 'nw-sponsor-logo';
          a.href = cfg.href;
          a.target = '_blank';
          a.rel = 'noopener sponsored';
          a.innerHTML = '<span class="sl-label"></span><img class="sl-logo" alt="Sponsor logo">';
          a.querySelector('.sl-label').textContent = cfg.label;
          a.querySelector('.sl-logo').src = cfg.logo;
          if (getComputedStyle(bc).position === 'static') bc.style.position = 'relative';
          bc.appendChild(a);
        } catch (e) {}
      }
      ensure();
      var n = 0;
      var iv = setInterval(function () {
        ensure();
        if (++n >= 400) clearInterval(iv);
      }, 300);
    }
    var s = D.createElement('script');
    s.id = 'nw-sponsor-logo-boot';
    s.textContent = '(' + watcher.toString() + ')(' + JSON.stringify(CFG) + ');';
    (D.head || D.documentElement).appendChild(s);
  } catch (e) { /* safeframed or cross-origin: do nothing */ }
})();
</script>"""

SNIPPET = (_SNIPPET
           .replace("__CSS__", json.dumps(_INJECT_CSS))
           .replace("__LABEL__", SPONSOR_LABEL)
           .replace("__LOGO__", LOGO_SRC))

# ── bootstrap snippet (inarticle1) ────────────────────────────────────────────
# The article templates define + fetch oop2 but never bind its div (Next.js
# hydration drops the server-rendered oop containers and the client tree
# doesn't include them), so the page's own googletag.display('dfp-ad-oop2')
# fails and the oop2 creative never executes. inarticle1 lives inside the
# article-body HTML blob (dangerouslySetInnerHTML), which hydration never
# reconciles — it renders on every article template. This creative rides it,
# collapses its own slot, binds the missing oop2 div, and re-triggers the
# render so the oop2 creative above serves normally.
BOOT_SNIPPET = """\
<script>
(function () {
  try {
    var T = window.top, D = T.document;
    /* 1. Collapse the carrier slot — the visible product is the oop2 logo. */
    try {
      var fe = window.frameElement;
      var slotDiv = fe && fe.closest('[id^="dfp-ad-"]');
      var wrap = slotDiv && (D.getElementById(slotDiv.id + '-wrapper') || slotDiv);
      if (wrap) wrap.style.display = 'none';
    } catch (e) {}
    /* 2. If oop2 already executed (page bound a div and won the race), stop. */
    if (D.getElementById('nw-sponsor-logo-boot')) return;
    /* 3. Bind the div the page never provided, then render the queued oop2 ad. */
    if (!D.getElementById('dfp-ad-oop2')) {
      var d = D.createElement('div');
      d.id = 'dfp-ad-oop2';
      D.body.appendChild(d);
    }
    var g = T.googletag;
    if (!g || !g.cmd) return;
    g.cmd.push(function () {
      try {
        var slot = g.pubads().getSlots().filter(function (s) {
          return s.getSlotElementId() === 'dfp-ad-oop2';
        })[0];
        if (!slot) return;
        g.display('dfp-ad-oop2');
        /* If the queued response was already consumed, force a fresh render. */
        T.setTimeout(function () {
          try {
            var div = D.getElementById('dfp-ad-oop2');
            if (div && !div.querySelector('iframe')) g.pubads().refresh([slot]);
          } catch (e) {}
        }, 1500);
      } catch (e) {}
    });
  } catch (e) { /* safeframed: do nothing */ }
})();
</script>"""

# ── services ──────────────────────────────────────────────────────────────────
li_svc   = client.GetService("LineItemService", version=V)
cr_svc   = client.GetService("CreativeService", version=V)
lica_svc = client.GetService("LineItemCreativeAssociationService", version=V)


def _one(resp):
    try:
        return (resp["results"] or [None])[0]
    except Exception:
        return None


def existing_li():
    stmt = (ad_manager.StatementBuilder(version=V)
            .Where("orderId = :o AND name = :n")
            .WithBindVariable("o", ORDER_ID).WithBindVariable("n", LI_NAME).Limit(1))
    return _one(li_svc.getLineItemsByStatement(stmt.ToStatement()))


def existing_creative(name):
    stmt = (ad_manager.StatementBuilder(version=V)
            .Where("name = :n").WithBindVariable("n", name).Limit(1))
    return _one(cr_svc.getCreativesByStatement(stmt.ToStatement()))


li = existing_li()
cr = existing_creative(CR_NAME)
boot_cr = existing_creative(BOOT_CR_NAME)

print("=" * 70)
print(f"ARTICLE SPONSOR LOGO SETUP — oop2  ({'DRY RUN' if DRY_RUN else 'APPLY'})")
print("=" * 70)
print(f"Ad units:  oop2 ({OOP2_AD_UNIT_ID}) + inarticle1 ({INARTICLE1_AD_UNIT_ID}) — no inventory changes")
print(f"Line item: {LI_NAME!r} on order {ORDER_ID}"
      + (f"  [exists: id={li['id']}]" if li else "  [will create]"))
print(f"Creative:  {CR_NAME!r} (out-of-page injection, SafeFrame off)"
      + (f"  [exists: id={cr['id']}]" if cr else "  [will create]"))
print(f"Creative:  {BOOT_CR_NAME!r} (slot-collapsing div bootstrap)"
      + (f"  [exists: id={boot_cr['id']}]" if boot_cr else "  [will create]"))
print()

if DRY_RUN:
    print("Re-run with --apply to create in GAM.")
    sys.exit(0)

# ── apply ─────────────────────────────────────────────────────────────────────
log = []

if li is None:
    print("Creating sponsorship line item...")
    li = li_svc.createLineItems([{
        "orderId": ORDER_ID,
        "name": LI_NAME,
        "lineItemType": "SPONSORSHIP",
        # Priority 3 outranks the incumbent oop2 sponsorship ("Logo 120x60
        # AI", LI 6986067522, default priority 4) so the demo wins its
        # KV-gated URLs. Real flights coordinate with the incumbent instead.
        "priority": 3,
        "costType": "CPD",
        "costPerUnit": {"currencyCode": "USD", "microAmount": 0},
        "startDateTimeType": "IMMEDIATELY",
        "unlimitedEndDateTime": True,
        "creativeRotationType": "EVEN",
        "skipInventoryCheck": True,
        "allowOverbook": True,
        "primaryGoal": {"goalType": "DAILY", "unitType": "IMPRESSIONS", "units": 100},
        # INTERSTITIAL = GAM's "Out of page" creative size. A plain 1x1
        # placeholder will NOT accept/serve an out-of-page creative. The
        # 300x250 placeholder routes inarticle1 requests to the bootstrap.
        "creativePlaceholders": [
            {"size": {"width": 1, "height": 1, "isAspectRatio": False},
             "creativeSizeType": "INTERSTITIAL"},
            {"size": {"width": 300, "height": 250, "isAspectRatio": False}},
        ],
        "targeting": {
            "inventoryTargeting": {
                "targetedAdUnits": [
                    {"adUnitId": OOP2_AD_UNIT_ID, "includeDescendants": True},
                    {"adUnitId": INARTICLE1_AD_UNIT_ID, "includeDescendants": True},
                ]
            }
        },
    }])[0]
print(f"  line_item_id={li['id']}  {li['name']}")
log.append({"type": "line_item", "id": li["id"], "name": li["name"]})

if cr is None:
    # Caveat: the API creates this as a plain 1x1 CustomCreative, which the
    # GAM UI does NOT treat as "Out of page" — it may refuse to serve into
    # an OOP slot. The reliable path is adding the creative from the line
    # item in the UI (creative size "Out of page"), which is how the live
    # creative 138563017162 was made. Kept here for the snippet template.
    print("Creating out-of-page custom creative...")
    cr = cr_svc.createCreatives([{
        "xsi_type": "CustomCreative",
        "name": CR_NAME,
        "advertiserId": ADVERTISER_ID,
        "size": {"width": 1, "height": 1, "isAspectRatio": False},
        "destinationUrl": CLICK_URL,
        "htmlSnippet": SNIPPET,
        "isSafeFrameCompatible": False,   # required: JS must reach the parent DOM
    }])[0]
print(f"  creative_id={cr['id']}  {cr['name']}")
log.append({"type": "creative", "id": cr["id"], "name": cr["name"]})

if boot_cr is None:
    print("Creating inarticle1 bootstrap creative...")
    boot_cr = cr_svc.createCreatives([{
        "xsi_type": "CustomCreative",
        "name": BOOT_CR_NAME,
        "advertiserId": ADVERTISER_ID,
        "size": {"width": 300, "height": 250, "isAspectRatio": False},
        "destinationUrl": CLICK_URL,   # not clickable; required field
        "htmlSnippet": BOOT_SNIPPET,
        "isSafeFrameCompatible": False,
    }])[0]
print(f"  bootstrap creative_id={boot_cr['id']}  {boot_cr['name']}")
log.append({"type": "creative", "id": boot_cr["id"], "name": boot_cr["name"]})

print("Creating LICAs...")
for c in (cr, boot_cr):
    try:
        lica = lica_svc.createLineItemCreativeAssociations([
            {"lineItemId": li["id"], "creativeId": c["id"]}
        ])[0]
        print(f"  LICA  li={lica['lineItemId']}  cr={lica['creativeId']}  "
              f"status={lica['status']}")
        log.append({"type": "lica", "li_id": lica["lineItemId"], "cr_id": lica["creativeId"]})
    except Exception as e:  # already associated on a re-run
        if "CommonError.ALREADY_EXISTS" in str(e):
            print(f"  LICA for cr={c['id']} already exists — skipping.")
        else:
            raise

log_path = Path("/tmp/article_sponsor_logo_log.json")
log_path.write_text(json.dumps(log, indent=2, default=str))
print(f"\nDone. IDs saved to {log_path}")
print("Next: approve the [TEST] line item in the GAM UI to preview on-site,")
print("then traffic the real sponsor flight per docs/article_sponsor_logo.md.")
