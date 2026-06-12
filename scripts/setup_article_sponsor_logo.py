"""Create the GAM side of the article-page sponsor logo, served through the
existing out-of-page unit `oop2` (/22541732127/newsweek/oop2) that is already
on every article page — no newsweek.com template change needed.

  1. [TEST] Sponsorship line item on Newsweek_Test-2 targeting oop2
  2. Out-of-page CustomCreative (SafeFrame OFF) whose JS injects a
     "Presented by <logo>" strip at the right of the article breadcrumb row
     (Autos | Volvo | Safety ............ Presented by [logo]).
     On pages without the breadcrumb row it renders nothing.
  3. LICA

The [TEST] creative uses an inline SVG placeholder logo. Real sponsor
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
# Engineering replaced oop2 with oop1 in the article ad config (2026-06-11)
# and ships a client-rendered container, so oop1 renders at first paint.
OOP1_AD_UNIT_ID = 23207087801        # /22541732127/newsweek/oop1 (out-of-page)

ORDER_ID      = 4082002976           # Newsweek_Test-2 (same as newsletter tests)
ADVERTISER_ID = 5131205161
LI_NAME       = "[TEST] Article Sponsor Logo - oop2"
CR_NAME       = "[TEST] Article Sponsor Logo - oop2"
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
# Two render modes:
#   - `sl-bc` (articles): injected at the right of the breadcrumb row.
#   - `sl-indiv` (templates without breadcrumbs that DO bind oop2, e.g. the
#     /ai section): centered strip inside the #dfp-ad-oop2 div itself, the
#     same placement the incumbent "Logo 120x60 AI" creative uses.
# Selectors match the hashed CSS-module classes by their stable prefix/suffix.
_INJECT_CSS = (
    "#nw-sponsor-logo{display:flex;align-items:center;gap:10px;text-decoration:none}"
    "#nw-sponsor-logo.sl-bc{position:absolute;right:0;top:50%;transform:translateY(-50%)}"
    "@media(max-width:767px){#nw-sponsor-logo.sl-bc{position:static;transform:none;"
    "width:100%;justify-content:flex-end;margin-top:8px}}"
    "#nw-sponsor-logo.sl-indiv{justify-content:center;width:100%;padding:3px 0}"
    "#nw-sponsor-logo.sl-wrap{position:static;transform:none;width:100%;"
    "justify-content:flex-end;margin-top:6px}"
    "#nw-sponsor-logo .sl-label{font-family:system-ui,-apple-system,sans-serif;"
    "font-size:11px;font-weight:500;letter-spacing:.08em;color:#68645a;"
    "text-transform:uppercase;white-space:nowrap}"
    "#nw-sponsor-logo .sl-logo{display:block;height:24px;width:auto;"
    "max-width:200px;object-fit:contain}"
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
      viewUrl: '',  /* agency DCM viewable-impression tracker, when provided */
      css: __CSS__
    };
    /* Runs in the PARENT document so hydration destroying this creative
       iframe cannot kill it. Articles: inject at the right of the
       breadcrumb row. Templates without breadcrumbs that bind oop2 (e.g.
       /ai): centered strip inside the oop2 div, like the incumbent.
       Re-injects whenever a re-render removes it; self-stops after ~2 min. */
    function watcher(cfg) {
      var d = document;
      /* MRC display viewability: >=50% in view for 1 continuous second.
         Fires once per pageview: dataLayer event + cfg.viewUrl tracker. */
      function armViewability(el) {
        try {
          var timer = null;
          var io = new IntersectionObserver(function (entries) {
            var e = entries[entries.length - 1];
            if (e.intersectionRatio >= 0.5) {
              if (!timer) timer = setTimeout(function () {
                io.disconnect();
                if (window.__nwSponsorViewable) return;
                window.__nwSponsorViewable = 1;
                try {
                  (window.dataLayer = window.dataLayer || []).push(
                    {event: 'nw_sponsor_logo_viewable', placement: 'breadcrumb'});
                } catch (x) {}
                if (cfg.viewUrl) {
                  var im = d.createElement('img');
                  im.src = cfg.viewUrl;
                  im.style.display = 'none';
                  im.width = im.height = 1;
                  d.body.appendChild(im);
                }
                var m = d.createElement('span');
                m.id = 'nw-sponsor-logo-viewed';
                m.style.display = 'none';
                d.body.appendChild(m);
              }, 1000);
            } else if (timer) { clearTimeout(timer); timer = null; }
          }, {threshold: [0, 0.5, 1]});
          io.observe(el);
        } catch (e) {}
      }
      function ensure() {
        try {
          if (d.getElementById('nw-sponsor-logo')) return;
          var bc = d.querySelector('[class*="ResponsiveBreadcrumbs"][class*="__container"]');
          var host = bc || d.getElementById('dfp-ad-oop2');
          if (!host) return;
          if (!d.getElementById('nw-sponsor-logo-css')) {
            var st = d.createElement('style');
            st.id = 'nw-sponsor-logo-css';
            st.textContent = cfg.css;
            d.head.appendChild(st);
          }
          var a = d.createElement('a');
          a.id = 'nw-sponsor-logo';
          a.className = bc ? 'sl-bc' : 'sl-indiv';
          a.href = cfg.href;
          a.target = '_blank';
          a.rel = 'noopener sponsored';
          a.innerHTML = '<span class="sl-label"></span><img class="sl-logo" alt="Sponsor logo">';
          a.querySelector('.sl-label').textContent = cfg.label;
          a.querySelector('.sl-logo').src = cfg.logo;
          if (bc && getComputedStyle(bc).position === 'static') bc.style.position = 'relative';
          host.appendChild(a);
          /* keyword-heavy breadcrumb rows: drop to an own right-aligned line */
          if (bc && getComputedStyle(a).position === 'absolute') {
            var kw = bc.querySelector('[class*="keywords"]');
            var kr = kw && kw.getBoundingClientRect();
            var sr = a.getBoundingClientRect();
            if (kr && kr.right > sr.left - 12) { a.className = 'sl-bc sl-wrap'; }
          }
          armViewability(a);
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

print("=" * 70)
print(f"ARTICLE SPONSOR LOGO SETUP — oop1  ({'DRY RUN' if DRY_RUN else 'APPLY'})")
print("=" * 70)
print(f"Ad unit:   oop1 (existing, id={OOP1_AD_UNIT_ID}) — no inventory changes")
print(f"Line item: {LI_NAME!r} on order {ORDER_ID}"
      + (f"  [exists: id={li['id']}]" if li else "  [will create]"))
print(f"Creative:  {CR_NAME!r} (out-of-page injection, SafeFrame off)"
      + (f"  [exists: id={cr['id']}]" if cr else "  [will create]"))
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
        # placeholder will NOT accept/serve an out-of-page creative.
        "creativePlaceholders": [{
            "size": {"width": 1, "height": 1, "isAspectRatio": False},
            "creativeSizeType": "INTERSTITIAL",
        }],
        "targeting": {
            "inventoryTargeting": {
                "targetedAdUnits": [
                    {"adUnitId": OOP1_AD_UNIT_ID, "includeDescendants": True}
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

print("Creating LICA...")
for c in (cr,):
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
