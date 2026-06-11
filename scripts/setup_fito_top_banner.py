"""Apple FITO demo: render the agency's 970x250 Innovid creative between
the article title and the video player, served deterministically via a
dedicated priority-3 LI on inarticle1 (desktop requests only).

This is the worked example of the GAM placement-injection pattern — see
docs/gam_placement_injection.md for the technique, gotchas, and lifecycle.
Created 2026-06-11: LI 7337440033 + creative 138562424408 on Newsweek_Test-2,
demo-gated to ?nwdemocr=06907703. Lookup-first; safe to re-run. After a
fresh --apply, the order must be re-approved in the GAM UI (new LIs sit
INACTIVE; the service account cannot ApproveOrders) and serving starts
~10 min later.

Usage:
    python3 scripts/setup_fito_top_banner.py           # dry run
    python3 scripts/setup_fito_top_banner.py --apply   # create in GAM
"""
import json, os, sys, tempfile, warnings
from pathlib import Path
warnings.filterwarnings("ignore")
DRY_RUN = "--apply" not in sys.argv
_env = Path(__file__).resolve().parent.parent / ".env"
for _line in _env.read_text().splitlines():
    _line = _line.strip()
    if not _line or _line.startswith("#") or "=" not in _line: continue
    _k, _v = _line.split("=", 1)
    os.environ.setdefault(_k.strip(), _v.strip().strip('"'))
from googleads import ad_manager, oauth2
_sa = json.loads(os.environ["GAM_SERVICE_ACCOUNT_JSON"])
with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as _f:
    json.dump(_sa, _f); _kf = _f.name
_oc = oauth2.GoogleServiceAccountClient(_kf, "https://www.googleapis.com/auth/dfp")
client = ad_manager.AdManagerClient(_oc, "NewsweekDashboard/1.0", network_code=os.environ["GAM_NETWORK_ID"])
V = "v202605"

ORDER_ID      = 4082002976
ADVERTISER_ID = 5131205161
INARTICLE1    = 23206070574
NWDEMOCR_KEY  = 14518983
NWDEMOCR_VAL  = 453183030636   # nwdemocr=06907703
LI_NAME = "[TEST] Apple FITO - top banner (video/title) relocation"
CR_NAME = "[TEST] Apple FITO - 970x250 relocated between title and video"

# The agency Innovid tag, embedded verbatim (GAM expands the macros in our
# snippet at serve time, same as it would in the third-party creative).
# </script> is split so it can't terminate our own script block.
SNIPPET = """\
<script>
(function () {
  try {
    var W = window.top, D = W.document;
    if (W.__nwAppleTop) return;
    var TAG = '<scr' + 'ipt src="https://rtr.innovid.com/js/r1.6a21f79f6a3a21.84321452?cb=%%CACHEBUSTER%%&ivc_click_through=%%CLICK_URL_ESC%%&gdpr=${GDPR}&gdpr_consent=${GDPR_CONSENT_452}"></scr' + 'ipt>';
    var tries = 0;
    var iv = setInterval(function () {
      tries++;
      if (tries > 20) { clearInterval(iv); return; }
      try {
        if (W.__nwAppleTop) { clearInterval(iv); return; }
        var vp = D.querySelector('[class*="VideoPlayer"][class*="__container"]') ||
                 (D.getElementById('nw-video-player') && D.getElementById('nw-video-player').parentElement);
        if (!vp || !vp.parentElement) return;
        W.__nwAppleTop = 1;
        clearInterval(iv);
        /* hide the carrier slot — the ad renders at the top position */
        try {
          var fe = window.frameElement;
          var slotDiv = fe && fe.closest('[id^="dfp-ad-"]');
          var wrap = slotDiv && (D.getElementById(slotDiv.id + '-wrapper') || slotDiv);
          if (wrap) wrap.style.display = 'none';
        } catch (e) {}
        /* full-width breakout container between title/dates and the video */
        var c = D.createElement('div');
        c.id = 'nw-apple-top';
        c.style.cssText = 'position:relative;width:100vw;left:50%;transform:translateX(-50%);' +
          'display:flex;justify-content:center;align-items:center;margin:16px 0;min-height:250px';
        var f = D.createElement('iframe');
        f.width = '970';
        f.height = '250';
        f.style.cssText = 'border:0;display:block';
        f.setAttribute('scrolling', 'no');
        f.title = 'Advertisement';
        c.appendChild(f);
        vp.parentElement.insertBefore(c, vp);
        var fd = f.contentWindow.document;
        fd.open();
        fd.write('<!doctype html><html><head><base target="_blank"></head>' +
                 '<body style="margin:0;padding:0">' + TAG + '</body></html>');
        fd.close();
      } catch (e) {}
    }, 300);
  } catch (e) { /* safeframed: do nothing */ }
})();
</script>"""

li_svc = client.GetService("LineItemService", version=V)
cr_svc = client.GetService("CreativeService", version=V)
lica_svc = client.GetService("LineItemCreativeAssociationService", version=V)
def one(resp):
    try: return (resp["results"] or [None])[0]
    except Exception: return None

li = one(li_svc.getLineItemsByStatement(
    ad_manager.StatementBuilder(version=V).Where("orderId = :o AND name = :n")
    .WithBindVariable("o", ORDER_ID).WithBindVariable("n", LI_NAME).Limit(1).ToStatement()))
cr_existing = one(cr_svc.getCreativesByStatement(
    ad_manager.StatementBuilder(version=V).Where("name = :n").WithBindVariable("n", CR_NAME).Limit(1).ToStatement()))
print("=" * 70)
print(f"APPLE FITO TOP BANNER  ({'DRY RUN' if DRY_RUN else 'APPLY'})")
print("=" * 70)
print(f"Line item: {LI_NAME!r}" + (f"  [exists: id={li['id']}]" if li else "  [will create]"))
print(f"Creative:  {CR_NAME!r}" + (f"  [exists: id={cr_existing['id']}]" if cr_existing else "  [will create]"))
if DRY_RUN:
    print("\nRe-run with --apply to create in GAM.")
    sys.exit(0)

if li is None:
    li = li_svc.createLineItems([{
        "orderId": ORDER_ID,
        "name": LI_NAME,
        "lineItemType": "SPONSORSHIP",
        "priority": 3,   # outranks the Apple takeover LI (p4) on inarticle1 only
        "costType": "CPD",
        "costPerUnit": {"currencyCode": "USD", "microAmount": 0},
        "startDateTimeType": "IMMEDIATELY",
        "unlimitedEndDateTime": True,
        "creativeRotationType": "EVEN",
        "roadblockingType": "ONE_OR_MORE",
        "skipInventoryCheck": True,
        "allowOverbook": True,
        "primaryGoal": {"goalType": "DAILY", "unitType": "IMPRESSIONS", "units": 100},
        "creativePlaceholders": [{"size": {"width": 970, "height": 250, "isAspectRatio": False}}],
        "targeting": {
            "inventoryTargeting": {"targetedAdUnits": [{"adUnitId": INARTICLE1, "includeDescendants": True}]},
            "customTargeting": {
                "xsi_type": "CustomCriteriaSet", "logicalOperator": "OR",
                "children": [{
                    "xsi_type": "CustomCriteriaSet", "logicalOperator": "AND",
                    "children": [{"xsi_type": "CustomCriteria", "keyId": NWDEMOCR_KEY,
                                  "valueIds": [NWDEMOCR_VAL], "operator": "IS"}],
                }],
            },
        },
    }])[0]
print(f"line_item_id={li['id']}  status={li['status']}")

cr = one(cr_svc.getCreativesByStatement(
    ad_manager.StatementBuilder(version=V).Where("name = :n").WithBindVariable("n", CR_NAME).Limit(1).ToStatement()))
if cr is None:
    cr = cr_svc.createCreatives([{
        "xsi_type": "CustomCreative",
        "name": CR_NAME,
        "advertiserId": ADVERTISER_ID,
        "size": {"width": 970, "height": 250, "isAspectRatio": False},
        "destinationUrl": "https://www.apple.com/iphone/",
        "htmlSnippet": SNIPPET,
        "isSafeFrameCompatible": False,
    }])[0]
print(f"creative_id={cr['id']}")
try:
    lica = lica_svc.createLineItemCreativeAssociations([{"lineItemId": li["id"], "creativeId": cr["id"]}])[0]
    print(f"LICA status={lica['status']}")
except Exception as e:
    if "ALREADY_EXISTS" in str(e): print("LICA already exists")
    else: raise
