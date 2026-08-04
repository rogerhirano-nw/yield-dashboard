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
LI_NAME = "[nw]_FITO-Fluid_POC_single-li-takeover_pp"
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
# Constraints discovered on earlier runs:
#  - service account cannot approve orders (OrderActionError.PERMISSION_DENIED,
#    run 30823071325) -> a fresh order stays DRAFT and never serves
#  - source order 4144745465 is managed/programmatic
#    (LineItemError.CANNOT_ADD_TO_MANAGED_ORDER, run 30823246802) -> cannot
#    host API-created line items
# So: reuse an existing APPROVED non-programmatic order under any [nw] test
# advertiser (the demo-gated test campaigns live in such orders).
order_svc = client.GetService("OrderService", version=VERSION)

nw_advs = []
resp = co_svc.getCompaniesByStatement(
    stmt("type = 'ADVERTISER' AND name LIKE '%[nw]%'"))
for c in getattr(resp, "results", None) or []:
    nw_advs.append(c)

order = None
for a in nw_advs:
    resp = order_svc.getOrdersByStatement(
        stmt("advertiserId = :a AND status = 'APPROVED'", a=a.id))
    for o in getattr(resp, "results", None) or []:
        if not getattr(o, "isProgrammatic", False) and not getattr(
                o, "isArchived", False):
            order = o
            break
    if order is not None:
        break

assert order is not None, (
    "No APPROVED non-programmatic [nw] test order found — approve the draft "
    f"POC order '{ORDER_NAME}' in the GAM UI, then re-run."
)
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
    # SPONSORSHIP is the real-world config, but activating a guaranteed LI
    # requires reservation rights the service account lacks
    # (LineItemOperationError.NOT_ALLOWED, run 30823644824). PRICE_PRIORITY
    # needs no reservation and $100 CPM wins the demo-gated auction, so the
    # single-LI takeover mechanism is testable all the same.
    li = li_svc.createLineItems([dict(
        base, lineItemType="PRICE_PRIORITY",
        costPerUnit={"currencyCode": "USD", "microAmount": 100000000},
        primaryGoal={"goalType": "NONE"})])[0]

    # best effort: archive the stranded INACTIVE sponsorship LI from run 4
    try:
        li_svc.performLineItemAction(
            {"xsi_type": "ArchiveLineItems"},
            stmt("id = :i", i=7389497908))
    except Exception as exc:
        print(f"archive of stranded sponsorship LI failed: {exc}")
summary["line_item"] = {"id": li.id, "type": str(li.lineItemType),
                        "status": str(li.status)}

# ---- 5. Build the fluid host creative --------------------------------------
def js_str(s):
    return json.dumps(s).replace("</", "<\\/")


snippet = """
<div id="fito-host" style="width:970px;height:250px;overflow:hidden"></div>
<script>
(function () {
  var FITO_POC_V = "v7b-cascade";
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

  // 2. KVP cascade: the anchor render flips page-level targeting, and every
  //    subsequent ad request (lazily-defined in-article slots, sticky, and —
  //    once dev wires it — the MUX player's VAST request) carries
  //    fitolive=fluid. Follower line items targeting that KVP win those
  //    auctions natively; nothing is painted or suppressed from here.
  //    (Production: the page sets this from its own slotRenderEnded listener
  //    after validating the anchor line item — creative-side is POC only.)
  try {
    var top = window.top;
    if (!top.__FITO_FLUID__) {
      top.__FITO_FLUID__ = true;
      if (top.googletag && top.googletag.pubads) {
        var pa = top.googletag.pubads();
        var cur = pa.getTargeting("nwdemocr") || [];
        if (cur.indexOf("fitolive") < 0) {
          cur.push("fitolive");
          pa.setTargeting("nwdemocr", cur);
        }
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
else:
    # refresh the snippet on re-runs so template fixes reach the live creative
    creative.htmlSnippet = snippet
    creative = cr_svc.updateCreatives([creative])[0]
    summary["creative_snippet_updated"] = True
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

# ---- 6b. Pre-roll: separate VIDEO_PLAYER line item for the MUX player -----
# The video must serve as a pre-roll through the player's own VAST request,
# so it cannot come from the display creative. Reuse the campaign video via
# a VastRedirectCreative pointing at its VAST tag.
PREROLL_LI_NAME = "[nw]_FITO-Fluid_POC_preroll_pp"
PREROLL_CR_NAME = "[nw]_FITO-Fluid_POC_preroll_vast_640x360"

pre_li = first(li_svc.getLineItemsByStatement(
    stmt("name = :n AND orderId = :o", n=PREROLL_LI_NAME, o=order.id)))
if pre_li is None:
    pre_li = li_svc.createLineItems([{
        "orderId": order.id,
        "name": PREROLL_LI_NAME,
        "lineItemType": "PRICE_PRIORITY",
        "environmentType": "VIDEO_PLAYER",
        "costType": "CPM",
        "costPerUnit": {"currencyCode": "USD", "microAmount": 100000000},
        "creativeRotationType": "EVEN",
        "primaryGoal": {"goalType": "NONE"},
        "startDateTimeType": "IMMEDIATELY",
        "endDateTime": {
            "date": {"year": 2026, "month": 8, "day": 17},
            "hour": 23, "minute": 59, "second": 0,
            "timeZoneId": "America/New_York",
        },
        "videoMaxDuration": 30000,
        "creativePlaceholders": [
            {"size": {"width": 640, "height": 360, "isAspectRatio": False}}],
        "targeting": {
            "inventoryTargeting": {
                "targetedAdUnits": [
                    {"adUnitId": root_ad_unit, "includeDescendants": True}]},
            "requestPlatformTargeting": {
                "targetedRequestPlatforms": ["VIDEO_PLAYER"]},
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
    }])[0]
summary["preroll_line_item"] = {"id": pre_li.id, "status": str(pre_li.status)}

# the campaign VAST tag is ~4KB — over vastXmlUrl's length limit — so the
# creative points at a short wrapper VAST hosted in this repo whose
# VASTAdTagURI carries the long tag (docs/snippets/fito_preroll_vast.xml)
WRAPPER_VAST_URL = ("https://raw.githubusercontent.com/rogerhirano-nw/"
                    "yield-dashboard/claude/fito-fluid-poc/docs/snippets/"
                    "fito_preroll_vast.xml")

pre_cr = first(cr_svc.getCreativesByStatement(
    stmt("name = :n AND advertiserId = :a",
         n=PREROLL_CR_NAME, a=order.advertiserId)))
if pre_cr is None:
    pre_cr = cr_svc.createCreatives([{
        "xsi_type": "VastRedirectCreative",
        "name": PREROLL_CR_NAME,
        "advertiserId": order.advertiserId,
        "size": {"width": 640, "height": 360, "isAspectRatio": False},
        "vastXmlUrl": WRAPPER_VAST_URL,
        "vastRedirectType": "LINEAR",
        "duration": 15000,
    }])[0]
if pre_cr is not None:
    summary["preroll_creative"] = {"id": pre_cr.id}
    pre_lica = first(lica_svc.getLineItemCreativeAssociationsByStatement(
        stmt("lineItemId = :l AND creativeId = :c",
             l=pre_li.id, c=pre_cr.id)))
    if pre_lica is None:
        lica_svc.createLineItemCreativeAssociations([
            {"lineItemId": pre_li.id, "creativeId": pre_cr.id}])
    summary["preroll_lica"] = "ok"

if str(pre_li.status) == "INACTIVE":
    try:
        li_svc.performLineItemAction(
            {"xsi_type": "ActivateLineItems"}, stmt("id = :i", i=pre_li.id))
    except Exception as exc:
        summary["preroll_activation_error"] = str(exc)[:200]

# ---- 6c. KVP cascade followers ---------------------------------------------
# The anchor creative sets page-level fitolive=fluid on render; follower LIs
# targeting that KVP win every subsequent slot natively (no painting).
FOLLOWER_LI_NAME = "[nw]_FITO-Fluid_POC_follower_cascade"
# the service account cannot create custom targeting KEYS (PERMISSION_DENIED,
# run 30832191951) — only values under existing keys. So the cascade signal is
# a second value on the existing nwdemocr key: anchor render appends
# nwdemocr=fitolive; followers target that value. (Production: a dedicated
# key created once in the UI.)
fkey = kv_key
fval = first(kv_svc.getCustomTargetingValuesByStatement(
    stmt("customTargetingKeyId = :k AND name = :v", k=fkey.id, v="fitolive")))
if fval is None:
    fval = kv_svc.createCustomTargetingValues([
        {"customTargetingKeyId": fkey.id, "name": "fitolive",
         "matchType": "EXACT"}])[0]
summary["cascade_kv"] = {"keyId": fkey.id, "valueId": fval.id}

fol_li = first(li_svc.getLineItemsByStatement(
    stmt("name = :n AND orderId = :o", n=FOLLOWER_LI_NAME, o=order.id)))
if fol_li is None:
    fol_li = li_svc.createLineItems([{
        "orderId": order.id,
        "name": FOLLOWER_LI_NAME,
        "lineItemType": "SPONSORSHIP",
        "costType": "CPM",
        "costPerUnit": {"currencyCode": "USD", "microAmount": 0},
        "creativeRotationType": "OPTIMIZED",
        "primaryGoal": {"goalType": "DAILY", "unitType": "IMPRESSIONS",
                        "units": 100},
        "startDateTimeType": "IMMEDIATELY",
        "endDateTime": {
            "date": {"year": 2026, "month": 8, "day": 17},
            "hour": 23, "minute": 59, "second": 0,
            "timeZoneId": "America/New_York",
        },
        "creativePlaceholders": [
            {"size": {"width": 970, "height": 250, "isAspectRatio": False}},
            {"size": {"width": 300, "height": 250, "isAspectRatio": False}},
            {"size": {"width": 728, "height": 90, "isAspectRatio": False}},
        ],
        "targeting": {
            "inventoryTargeting": {
                "targetedAdUnits": [
                    {"adUnitId": root_ad_unit, "includeDescendants": True}]},
            "customTargeting": {
                "xsi_type": "CustomCriteriaSet",
                "logicalOperator": "AND",
                "children": [{
                    "xsi_type": "CustomCriteria",
                    "keyId": fkey.id,
                    "valueIds": [fval.id],
                    "operator": "IS",
                }],
            },
        },
        "skipInventoryCheck": True,
        "allowOverbook": True,
    }])[0]
summary["follower_line_item"] = {"id": fol_li.id, "status": str(fol_li.status)}

fol_creatives = []
for size_label, (w, h) in {"970x250": (970, 250), "300x250": (300, 250),
                           "728x90": (728, 90)}.items():
    tag = tags.get(size_label)
    if not tag:
        continue
    cname = f"[nw]_FITO-Fluid_POC_follower_{size_label}"
    c = first(cr_svc.getCreativesByStatement(
        stmt("name = :n AND advertiserId = :a",
             n=cname, a=order.advertiserId)))
    if c is None:
        c = cr_svc.createCreatives([{
            "xsi_type": "ThirdPartyCreative",
            "name": cname,
            "advertiserId": order.advertiserId,
            "size": {"width": w, "height": h, "isAspectRatio": False},
            "snippet": tag,
            "isSafeFrameCompatible": True,
        }])[0]
    fol_creatives.append(c.id)
    lica = first(lica_svc.getLineItemCreativeAssociationsByStatement(
        stmt("lineItemId = :l AND creativeId = :c", l=fol_li.id, c=c.id)))
    if lica is None:
        lica_svc.createLineItemCreativeAssociations([
            {"lineItemId": fol_li.id, "creativeId": c.id}])
summary["follower_creatives"] = fol_creatives

if str(fol_li.status) == "INACTIVE":
    try:
        li_svc.performLineItemAction(
            {"xsi_type": "ActivateLineItems"}, stmt("id = :i", i=fol_li.id))
    except Exception as exc:
        summary["follower_activation_error"] = str(exc)[:200]

# ---- 6d. Organic test page: no URL-param gate --------------------------------
# Anchor eligibility comes from the test article itself (article_id KV the
# page already passes); the cascade signal (nwdemocr=fitolive) is set by the
# creative on render, so followers and the pre-roll need no gate at all.
TEST_ARTICLE_ID = "12233005"  # Insta360 test article
try:
    akey = first(kv_svc.getCustomTargetingKeysByStatement(
        stmt("name = :n", n="article_id")))
    assert akey is not None, "custom targeting key article_id not found"
    aval = first(kv_svc.getCustomTargetingValuesByStatement(
        stmt("customTargetingKeyId = :k AND name = :v",
             k=akey.id, v=TEST_ARTICLE_ID)))
    if aval is None:
        aval = kv_svc.createCustomTargetingValues([
            {"customTargetingKeyId": akey.id, "name": TEST_ARTICLE_ID,
             "matchType": "EXACT"}])[0]

    anchor = first(li_svc.getLineItemsByStatement(stmt("id = :i", i=li.id)))
    if f"'{akey.id}'" not in str(anchor.targeting) and \
            str(akey.id) not in str(anchor.targeting):
        anchor.targeting.customTargeting = {
            "xsi_type": "CustomCriteriaSet",
            "logicalOperator": "AND",
            "children": [
                {"xsi_type": "CustomCriteria", "keyId": akey.id,
                 "valueIds": [aval.id], "operator": "IS"},
                # exclusion keeps the anchor off follower slots (its own
                # request never carries fitolive; post-render requests do)
                {"xsi_type": "CustomCriteria", "keyId": fkey.id,
                 "valueIds": [fval.id], "operator": "IS_NOT"},
            ],
        }
        anchor.skipInventoryCheck = True
        anchor.allowOverbook = True
        li_svc.updateLineItems([anchor])
        summary["anchor_retargeted_to_article"] = TEST_ARTICLE_ID
    else:
        summary["anchor_retargeted_to_article"] = "already targeted"

    # pre-roll follows the cascade signal, not the URL gate
    pre = first(li_svc.getLineItemsByStatement(stmt("id = :i", i=pre_li.id)))
    if str(fval.id) not in str(pre.targeting):
        pre.targeting.customTargeting = {
            "xsi_type": "CustomCriteriaSet",
            "logicalOperator": "AND",
            "children": [
                {"xsi_type": "CustomCriteria", "keyId": fkey.id,
                 "valueIds": [fval.id], "operator": "IS"},
            ],
        }
        pre.skipInventoryCheck = True
        pre.allowOverbook = True
        li_svc.updateLineItems([pre])
        summary["preroll_retargeted_to_cascade"] = True
    else:
        summary["preroll_retargeted_to_cascade"] = "already targeted"
except Exception as exc:
    summary["organic_retarget_error"] = str(exc)[:300]

if str(order.status) in ("DRAFT", "PENDING_APPROVAL"):
    try:
        r = order_svc.performOrderAction(
            {"xsi_type": "ApproveOrders"}, stmt("id = :i", i=order.id))
        summary["order_approval_changes"] = int(getattr(r, "numChanges", 0))
    except Exception as exc:
        summary["order_approval_error"] = str(exc)[:300]

li = first(li_svc.getLineItemsByStatement(stmt("id = :i", i=li.id)))
if str(li.status) == "INACTIVE":
    try:
        r = li_svc.performLineItemAction(
            {"xsi_type": "ActivateLineItems"}, stmt("id = :i", i=li.id))
        summary["li_activation_changes"] = int(getattr(r, "numChanges", 0))
        li = first(li_svc.getLineItemsByStatement(stmt("id = :i", i=li.id)))
    except Exception as exc:
        summary["li_activation_error"] = str(exc)[:300]
summary["line_item_final_status"] = str(li.status)
summary["test_url_param"] = f"?{KV_KEY_NAME}={KV_VALUE}"

# ---- diagnostics: live state of every piece --------------------------------
diag = {}
resp = kv_svc.getCustomTargetingKeysByStatement(
    stmt("name = :n", n="article_id"))
diag["article_id_keys"] = [
    {"id": k.id, "type": str(getattr(k, "type", "")),
     "status": str(getattr(k, "status", ""))}
    for k in (getattr(resp, "results", None) or [])]
diag["article_id_key_used"] = None
try:
    diag["article_id_key_used"] = akey.id
    vresp = kv_svc.getCustomTargetingValuesByStatement(
        stmt("customTargetingKeyId = :k AND name = :v",
             k=akey.id, v=TEST_ARTICLE_ID))
    diag["article_value"] = [
        {"id": v.id, "status": str(getattr(v, "status", "")),
         "matchType": str(getattr(v, "matchType", ""))}
        for v in (getattr(vresp, "results", None) or [])]
except NameError:
    pass
for label, lid in (("anchor", li.id), ("preroll", pre_li.id),
                   ("follower", fol_li.id)):
    x = first(li_svc.getLineItemsByStatement(stmt("id = :i", i=lid)))
    diag[label] = {
        "id": lid,
        "status": str(x.status),
        "isMissingCreatives": getattr(x, "isMissingCreatives", None),
        "targeting": str(getattr(x.targeting, "customTargeting", ""))[:600],
    }
print("\n=== DIAG ===")
print(json.dumps(diag, indent=1, default=str))

print("\n=== SUMMARY ===")
print(json.dumps(summary, indent=1, default=str))
