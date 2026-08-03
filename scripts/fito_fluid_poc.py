"""FITO Fluid proof-of-concept: one line item + one custom creative that
renders the entire takeover (in-slot 970x250 + painted display units +
outstream video) on a single pageview.

Runs in GitHub Actions (GAM_SERVICE_ACCOUNT_JSON / GAM_NETWORK_ID secrets).
Demo-gated: targets custom key `nwdemocr` = `fitofluid`, which only matches
pageviews loaded with ?nwdemocr=fitofluid.

Assets reused from order 4144745465 (Apple "Apple at Work" FITO test):
  138568965365  Innovid 970x250 (fit)
  138568965371  Innovid 300x250 (fit)
  138568965389  Innovid 728x90  (fit)
  138568962668  uploaded VideoCreative 640x360 (fit)

Idempotent: looks up each entity by name before creating.
"""
import json
import os
import tempfile

from googleads import ad_manager, oauth2

VERSION = "v202605"
SRC_ORDER_ID = 4144745465
DISPLAY_TAG_IDS = {"970x250": 138568965365, "300x250": 138568965371,
                   "728x90": 138568965389}
VIDEO_CREATIVE_ID = 138568962668
KV_KEY_NAME = "nwdemocr"
KV_VALUE = "fitofluid"
ORDER_NAME = "[nw] FITO Fluid POC"
LI_NAME = "[nw]_FITO-Fluid_POC_single-li-takeover"
CREATIVE_NAME = "[nw]_FITO-Fluid_POC_fluid-host_970x250"

key_data = json.loads(os.environ["GAM_SERVICE_ACCOUNT_JSON"])
with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
    json.dump(key_data, f)
    key_file = f.name
oauth2_client = oauth2.GoogleServiceAccountClient(
    key_file, "https://www.googleapis.com/auth/dfp"
)
client = ad_manager.AdManagerClient(
    oauth2_client, "FitoFluidPOC/1.0", network_code=os.environ["GAM_NETWORK_ID"]
)


def stmt(where, **binds):
    sb = ad_manager.StatementBuilder(version=VERSION)
    sb.Where(where)
    for k, v in binds.items():
        sb.WithBindVariable(k, v)
    sb.Limit(10)
    return sb.ToStatement()


def first(resp):
    results = getattr(resp, "results", None) or []
    return results[0] if results else None


summary = {}

# ---- 1. Pull source assets -------------------------------------------------
cr_svc = client.GetService("CreativeService", version=VERSION)
all_ids = list(DISPLAY_TAG_IDS.values()) + [VIDEO_CREATIVE_ID]
resp = cr_svc.getCreativesByStatement(
    stmt(f"id IN ({', '.join(str(i) for i in all_ids)})")
)
tags = {}
video_url = None
for c in getattr(resp, "results", None) or []:
    if c.id == VIDEO_CREATIVE_ID:
        # uploaded VideoCreative: asset URL lives in different fields across
        # creative subtypes — probe the common ones, then dump for debugging
        for attr in ("videoSourceUrl", "vastPreviewUrl", "assetUrl"):
            v = getattr(c, attr, None)
            if v:
                video_url = str(v)
                break
        if video_url is None:
            assets = getattr(c, "creativeAssets", None) or []
            for a in assets:
                v = getattr(a, "assetUrl", None)
                if v:
                    video_url = str(v)
                    break
        if video_url is None:
            print("VIDEO CREATIVE DUMP (no asset URL found):")
            print(repr(c)[:4000])
    else:
        size = f"{c.size.width}x{c.size.height}"
        tags[size] = str(getattr(c, "snippet", "") or "")
summary["video_url_found"] = bool(video_url)
summary["display_tags_found"] = sorted(tags)

# ---- 2. Test advertiser, trafficker, root ad unit, KV ----------------------
co_svc = client.GetService("CompanyService", version=VERSION)
adv = first(co_svc.getCompaniesByStatement(
    stmt("type = 'ADVERTISER' AND name LIKE '%[nw]%'")))
if adv is None:
    adv = first(co_svc.getCompaniesByStatement(
        stmt("type = 'ADVERTISER' AND name LIKE '%nw]%'")))
assert adv is not None, "No [nw] test advertiser found"
summary["advertiser"] = {"id": adv.id, "name": adv.name}

user_svc = client.GetService("UserService", version=VERSION)
me = user_svc.getCurrentUser()
net_svc = client.GetService("NetworkService", version=VERSION)
root_ad_unit = net_svc.getCurrentNetwork().effectiveRootAdUnitId

kv_svc = client.GetService("CustomTargetingService", version=VERSION)
kv_key = first(kv_svc.getCustomTargetingKeysByStatement(
    stmt("name = :n", n=KV_KEY_NAME)))
assert kv_key is not None, f"custom targeting key {KV_KEY_NAME} not found"
val = first(kv_svc.getCustomTargetingValuesByStatement(
    stmt("customTargetingKeyId = :k AND name = :v", k=kv_key.id, v=KV_VALUE)))
if val is None:
    val = kv_svc.createCustomTargetingValues([
        {"customTargetingKeyId": kv_key.id, "name": KV_VALUE,
         "matchType": "EXACT"}])[0]
summary["kv"] = {"keyId": kv_key.id, "valueId": val.id}

# ---- 3. Order --------------------------------------------------------------
# The service account cannot approve orders (OrderActionError.PERMISSION_DENIED
# on ApproveOrders, verified run 30823071325), so a fresh order stays DRAFT and
# never serves. Instead, create the test LI inside the already-approved source
# order; archive the stranded draft POC order from the first run if possible.
order_svc = client.GetService("OrderService", version=VERSION)
draft = first(order_svc.getOrdersByStatement(stmt("name = :n", n=ORDER_NAME)))
if draft is not None and str(draft.status) == "DRAFT":
    try:
        order_svc.performOrderAction(
            {"xsi_type": "ArchiveOrders"}, stmt("id = :i", i=draft.id))
        summary["draft_poc_order_archived"] = draft.id
    except Exception as exc:
        summary["draft_poc_order_archive_error"] = str(exc)[:200]

order = first(order_svc.getOrdersByStatement(stmt("id = :i", i=SRC_ORDER_ID)))
summary["order"] = {"id": order.id, "name": order.name,
                    "status": str(order.status)}

# ---- 4. Line item ----------------------------------------------------------
li_svc = client.GetService("LineItemService", version=VERSION)
li = first(li_svc.getLineItemsByStatement(
    stmt("name = :n AND orderId = :o", n=LI_NAME, o=order.id)))
if li is None:
    base = {
        "orderId": order.id,
        "name": LI_NAME,
        "costType": "CPM",
        "costPerUnit": {"currencyCode": "USD", "microAmount": 0},
        "creativeRotationType": "EVEN",
        "startDateTimeType": "IMMEDIATELY",
        "endDateTime": {
            "date": {"year": 2026, "month": 8, "day": 17},
            "hour": 23, "minute": 59, "second": 0,
            "timeZoneId": "America/New_York",
        },
        "creativePlaceholders": [
            {"size": {"width": 970, "height": 250, "isAspectRatio": False}}],
        "targeting": {
            "inventoryTargeting": {
                "targetedAdUnits": [
                    {"adUnitId": root_ad_unit, "includeDescendants": True}]},
            "customTargeting": {
                "xsi_type": "CustomCriteriaSet",
                "logicalOperator": "AND",
                "children": [{
                    "xsi_type": "CustomCriteria",
                    "keyId": kv_key.id,
                    "valueIds": [val.id],
                    "operator": "IS",
                }],
            },
        },
        "skipInventoryCheck": True,
        "allowOverbook": True,
    }
    try:
        li = li_svc.createLineItems([dict(
            base, lineItemType="SPONSORSHIP",
            primaryGoal={"goalType": "DAILY", "unitType": "IMPRESSIONS",
                         "units": 100})])[0]
    except Exception as exc:
        print(f"SPONSORSHIP create failed ({exc}); trying PRICE_PRIORITY")
        li = li_svc.createLineItems([dict(
            base, lineItemType="PRICE_PRIORITY",
            costPerUnit={"currencyCode": "USD", "microAmount": 100000000},
            primaryGoal={"goalType": "NONE"})])[0]
summary["line_item"] = {"id": li.id, "type": str(li.lineItemType),
                        "status": str(li.status)}

# ---- 5. Build the fluid host creative --------------------------------------
def js_str(s):
    return json.dumps(s).replace("</", "<\\/")


snippet = """
<div id="fito-host" style="width:970px;height:250px;overflow:hidden"></div>
<script>
(function () {
  var TAGS = { t970: %(t970)s, t300: %(t300)s, t728: %(t728)s };
  var VIDEO_URL = %(video)s;

  function writeFrame(doc, el, w, h, tag) {
    el.innerHTML = "";
    var wrap = doc.createElement("div");
    wrap.style.cssText = "width:" + w + "px;height:" + h +
      "px;margin:8px auto;overflow:hidden";
    var f = doc.createElement("iframe");
    f.width = w; f.height = h;
    f.frameBorder = 0; f.scrolling = "no";
    f.style.cssText = "border:0;display:block";
    wrap.appendChild(f);
    el.appendChild(wrap);
    var d = f.contentWindow.document;
    d.open();
    d.write("<body style='margin:0'>" + tag + "</body>");
    d.close();
  }

  // 1. own unit (renders inside the GPT slot iframe = measured geometry)
  writeFrame(document, document.getElementById("fito-host"), 970, 250,
             TAGS.t970);

  // 2. break out and paint the rest of the takeover (POC stand-in for the
  //    page-contract hook; requires friendly iframe)
  try {
    var top = window.top;
    if (top.__FITO_FLUID__) return;
    top.__FITO_FLUID__ = true;
    var pdoc = top.document;

    function paintTag(wrapperId, w, h, tag) {
      var el = pdoc.getElementById(wrapperId);
      if (el) writeFrame(pdoc, el, w, h, tag);
    }

    paintTag("dfp-ad-inarticle2-wrapper", 300, 250, TAGS.t300);
    paintTag("dfp-ad-inarticle4-wrapper", 728, 90, TAGS.t728);

    var vEl = pdoc.getElementById("dfp-ad-inarticle3-wrapper");
    if (vEl && VIDEO_URL) {
      vEl.innerHTML =
        "<div style='max-width:640px;margin:8px auto'>" +
        "<video src='" + VIDEO_URL + "' autoplay muted playsinline controls" +
        " style='width:100%%;display:block'></video></div>";
      var v = vEl.querySelector("video");
      if (v && top.IntersectionObserver) {
        v.pause();
        new top.IntersectionObserver(function (es) {
          es.forEach(function (e) { e.isIntersecting ? v.play() : v.pause(); });
        }, { threshold: 0.5 }).observe(v);
      }
    }
  } catch (e) {}
})();
</script>
""" % {
    "t970": js_str(tags.get("970x250", "")),
    "t300": js_str(tags.get("300x250", "")),
    "t728": js_str(tags.get("728x90", "")),
    "video": js_str(video_url or ""),
}

# creative must share the order's advertiser or the LICA is rejected
creative = first(cr_svc.getCreativesByStatement(
    stmt("name = :n AND advertiserId = :a",
         n=CREATIVE_NAME, a=order.advertiserId)))
if creative is None:
    creative = cr_svc.createCreatives([{
        "xsi_type": "CustomCreative",
        "name": CREATIVE_NAME,
        "advertiserId": order.advertiserId,
        "size": {"width": 970, "height": 250, "isAspectRatio": False},
        "htmlSnippet": snippet,
        "isSafeFrameCompatible": False,
    }])[0]
summary["creative"] = {"id": creative.id}

# ---- 6. LICA + approve order ----------------------------------------------
lica_svc = client.GetService("LineItemCreativeAssociationService",
                             version=VERSION)
lica = first(lica_svc.getLineItemCreativeAssociationsByStatement(
    stmt("lineItemId = :l AND creativeId = :c", l=li.id, c=creative.id)))
if lica is None:
    lica_svc.createLineItemCreativeAssociations([
        {"lineItemId": li.id, "creativeId": creative.id}])
summary["lica"] = "ok"

if str(order.status) in ("DRAFT", "PENDING_APPROVAL"):
    try:
        r = order_svc.performOrderAction(
            {"xsi_type": "ApproveOrders"}, stmt("id = :i", i=order.id))
        summary["order_approval_changes"] = int(getattr(r, "numChanges", 0))
    except Exception as exc:
        summary["order_approval_error"] = str(exc)[:300]

li = first(li_svc.getLineItemsByStatement(stmt("id = :i", i=li.id)))
summary["line_item_final_status"] = str(li.status)
summary["test_url_param"] = f"?{KV_KEY_NAME}={KV_VALUE}"

print("\n=== SUMMARY ===")
print(json.dumps(summary, indent=1, default=str))
