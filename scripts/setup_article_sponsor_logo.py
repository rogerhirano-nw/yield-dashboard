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
OOP2_AD_UNIT_ID = 23207098418        # /22541732127/newsweek/oop2 (out-of-page)

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
# NOT SafeFrame-compatible, so the JS can reach the parent document. It
# injects the strip at the right of the breadcrumb row and bails silently on
# any page that doesn't have one (self-scoping to article pages).
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
    var doc = window.top.document;
    if (doc.getElementById('nw-sponsor-logo')) return;
    var bc = doc.querySelector('[class*="ResponsiveBreadcrumbs"][class*="__container"]');
    if (!bc) return;  /* not an article page: render nothing */
    if (!doc.getElementById('nw-sponsor-logo-css')) {
      var st = doc.createElement('style');
      st.id = 'nw-sponsor-logo-css';
      st.textContent = __CSS__;
      doc.head.appendChild(st);
    }
    var a = doc.createElement('a');
    a.id = 'nw-sponsor-logo';
    a.href = '%%CLICK_URL_UNESC%%%%DEST_URL%%';
    a.target = '_blank';
    a.rel = 'noopener sponsored';
    a.innerHTML = '<span class="sl-label">__LABEL__</span>' +
      '<img class="sl-logo" src="__LOGO__" alt="Sponsor logo">';
    if (window.top.getComputedStyle(bc).position === 'static') {
      bc.style.position = 'relative';
    }
    bc.appendChild(a);
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


def existing_creative():
    stmt = (ad_manager.StatementBuilder(version=V)
            .Where("name = :n").WithBindVariable("n", CR_NAME).Limit(1))
    return _one(cr_svc.getCreativesByStatement(stmt.ToStatement()))


li = existing_li()
cr = existing_creative()

print("=" * 70)
print(f"ARTICLE SPONSOR LOGO SETUP — oop2  ({'DRY RUN' if DRY_RUN else 'APPLY'})")
print("=" * 70)
print(f"Ad unit:   oop2 (existing, id={OOP2_AD_UNIT_ID}) — no inventory changes")
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
        "costType": "CPD",
        "costPerUnit": {"currencyCode": "USD", "microAmount": 0},
        "startDateTimeType": "IMMEDIATELY",
        "unlimitedEndDateTime": True,
        "creativeRotationType": "EVEN",
        "skipInventoryCheck": True,
        "allowOverbook": True,
        "primaryGoal": {"goalType": "DAILY", "unitType": "IMPRESSIONS", "units": 100},
        "creativePlaceholders": [{
            "size": {"width": 1, "height": 1, "isAspectRatio": False},
        }],
        "targeting": {
            "inventoryTargeting": {
                "targetedAdUnits": [
                    {"adUnitId": OOP2_AD_UNIT_ID, "includeDescendants": True}
                ]
            }
        },
    }])[0]
print(f"  line_item_id={li['id']}  {li['name']}")
log.append({"type": "line_item", "id": li["id"], "name": li["name"]})

if cr is None:
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
try:
    lica = lica_svc.createLineItemCreativeAssociations([
        {"lineItemId": li["id"], "creativeId": cr["id"]}
    ])[0]
    print(f"  LICA  li={lica['lineItemId']}  cr={lica['creativeId']}  "
          f"status={lica['status']}")
    log.append({"type": "lica", "li_id": lica["lineItemId"], "cr_id": lica["creativeId"]})
except Exception as e:  # already associated on a re-run
    if "CommonError.ALREADY_EXISTS" in str(e):
        print("  LICA already exists — skipping.")
    else:
        raise

log_path = Path("/tmp/article_sponsor_logo_log.json")
log_path.write_text(json.dumps(log, indent=2, default=str))
print(f"\nDone. IDs saved to {log_path}")
print("Next: approve the [TEST] line item in the GAM UI to preview on-site,")
print("then traffic the real sponsor flight per docs/article_sponsor_logo.md.")
