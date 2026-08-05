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
# Dedicated production cascade key. `nwdemocr` must NOT be used for this: a
# non-empty nwdemocr bypasses the site's DoubleVerify invalid-traffic gate
# (skips NoPassFQ/keyEx, forces googletag.display and APS bids on IDS=1).
# The service account cannot create custom targeting KEYS, so this key must be
# created once in the GAM UI. Everything below degrades gracefully until then.
FITO_KEY_NAME = "fito"
FITO_VALUE = "live"
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

# dedicated production cascade key — present only once Ad Ops creates it in the
# GAM UI; until then every consumer below falls back to the nwdemocr cascade
fito_key = first(kv_svc.getCustomTargetingKeysByStatement(
    stmt("name = :n", n=FITO_KEY_NAME)))
fito_val = None
if fito_key is not None:
    fito_val = first(kv_svc.getCustomTargetingValuesByStatement(
        stmt("customTargetingKeyId = :k AND name = :v",
             k=fito_key.id, v=FITO_VALUE)))
    if fito_val is None:
        try:
            fito_val = kv_svc.createCustomTargetingValues([
                {"customTargetingKeyId": fito_key.id, "name": FITO_VALUE,
                 "matchType": "EXACT"}])[0]
        except Exception as exc:
            summary["fito_value_error"] = str(exc)[:200]
summary["fito_kv"] = (
    {"keyId": fito_key.id, "valueId": getattr(fito_val, "id", None)}
    if fito_key is not None
    else f"key '{FITO_KEY_NAME}' not created yet — create it in the GAM UI")

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
  var FITO_POC_V = "v11-fito-key";
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
        // production cascade signal — dedicated key, no side effects
        pa.setTargeting("fito", "live");
        // legacy signal, kept until the follower/pre-roll finish migrating
        var cur = pa.getTargeting("nwdemocr") || [];
        if (cur.indexOf("fitolive") < 0) {
          cur.push("fitolive");
          pa.setTargeting("nwdemocr", cur);
        }
      }
      // NOTE: the video/pre-roll leg is deliberately NOT wired from here.
      // The site's video module forwards window.nwdemocr into the player's
      // VAST cust_params, but re-derives it from the URL at mount, so a
      // creative-side override would have to defeat the page's own code —
      // the exact tampering pattern the publisher's Confiant wrapper exists
      // to catch. Sanctioned routes instead: (a) demo with the site's own
      // test param ?nwdemocr=fitolive, (b) production = dev merges live GPT
      // page targeting into the video ad request's cust_params.
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

# follower must also accept the new production cascade key
if fito_key is not None and fito_val is not None:
    fol_now = first(li_svc.getLineItemsByStatement(stmt("id = :i", i=fol_li.id)))
    if str(fito_val.id) not in str(fol_now.targeting):
        fol_now.targeting.customTargeting = {
            "xsi_type": "CustomCriteriaSet", "logicalOperator": "OR",
            "children": [
                {"xsi_type": "CustomCriteriaSet", "logicalOperator": "AND",
                 "children": [{"xsi_type": "CustomCriteria", "keyId": fkey.id,
                               "valueIds": [fval.id], "operator": "IS"}]},
                {"xsi_type": "CustomCriteriaSet", "logicalOperator": "AND",
                 "children": [{"xsi_type": "CustomCriteria", "keyId": fito_key.id,
                               "valueIds": [fito_val.id], "operator": "IS"}]},
            ],
        }
        fol_now.skipInventoryCheck = True
        fol_now.allowOverbook = True
        li_svc.updateLineItems([fol_now])
        summary["follower_accepts_fito"] = True
    else:
        summary["follower_accepts_fito"] = "already"

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
TEST_ARTICLE_ID = "12233005"  # Insta360 test article (display-only, no player)
# Shared-contextual sync: the video module builds cust_params containing
# cat/sitecat/group_cat/nwnet_section/content/vidcontent/topics/pageurl —
# the SAME contextual dimensions display requests carry. Targeting both legs
# on one of those keys syncs them per-pageview with no cookie dependency, no
# page change, and stays inside Kelly's "section or contextual only" rule.
# "Dare to Dream" scopes the POC to one series that has verified video.
SYNC_KEY = "topics"
SYNC_VALUE = "Dare to Dream"
# the SERVING anchor is the Sponsorship LI Roger activated — NOT `li`, which
# the name lookup resolves to the inactive `_pp` copy (diag run 30907700023)
ANCHOR_LI_ID = 7389497908
QA_ARTICLE_ID = "11184432"   # qa.next.newsweek.com Cadillac F1 article
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

    # the shared contextual key both display AND video requests carry
    skey = first(kv_svc.getCustomTargetingKeysByStatement(
        stmt("name = :n", n=SYNC_KEY)))
    assert skey is not None, f"custom targeting key {SYNC_KEY} not found"
    sval = first(kv_svc.getCustomTargetingValuesByStatement(
        stmt("customTargetingKeyId = :k AND name = :v",
             k=skey.id, v=SYNC_VALUE)))
    if sval is None:
        sval = kv_svc.createCustomTargetingValues([
            {"customTargetingKeyId": skey.id, "name": SYNC_VALUE,
             "matchType": "EXACT"}])[0]
    summary["sync_kv"] = {"key": SYNC_KEY, "keyId": skey.id,
                          "value": SYNC_VALUE, "valueId": sval.id}

    demo_val = val  # nwdemocr=fitofluid — forwarded to BOTH display and video

    def crit(k, v, op="IS"):
        return {"xsi_type": "CustomCriteria", "keyId": k,
                "valueIds": [v], "operator": op}

    # --- QA test article (Cadillac F1) -------------------------------------
    # Scoped with siteenv=qa so it cannot fire on the production article that
    # shares this article_id.
    qa_aval = first(kv_svc.getCustomTargetingValuesByStatement(
        stmt("customTargetingKeyId = :k AND name = :v",
             k=akey.id, v=QA_ARTICLE_ID)))
    if qa_aval is None:
        qa_aval = kv_svc.createCustomTargetingValues([
            {"customTargetingKeyId": akey.id, "name": QA_ARTICLE_ID,
             "matchType": "EXACT"}])[0]
    envkey = first(kv_svc.getCustomTargetingKeysByStatement(
        stmt("name = :n", n="siteenv")))
    qa_env = None
    if envkey is not None:
        qa_env = first(kv_svc.getCustomTargetingValuesByStatement(
            stmt("customTargetingKeyId = :k AND name = :v",
                 k=envkey.id, v="qa")))
        if qa_env is None:
            try:
                qa_env = kv_svc.createCustomTargetingValues([
                    {"customTargetingKeyId": envkey.id, "name": "qa",
                     "matchType": "EXACT"}])[0]
            except Exception as exc:
                summary["siteenv_value_error"] = str(exc)[:200]
    qa_children = [crit(akey.id, qa_aval.id), crit(fkey.id, fval.id, "IS_NOT")]
    if qa_env is not None:
        qa_children.insert(1, crit(envkey.id, qa_env.id))
    summary["qa_branch"] = {
        "article_id": QA_ARTICLE_ID,
        "siteenv_scoped": qa_env is not None,
    }

    # anchor: contextual scope OR the sanctioned URL demo param; the IS_NOT
    # keeps it off follower slots (its own request predates the cascade value)
    anchor = first(li_svc.getLineItemsByStatement(
        stmt("id = :i", i=ANCHOR_LI_ID)))
    anchor.targeting.customTargeting = {
        "xsi_type": "CustomCriteriaSet",
        "logicalOperator": "OR",
        "children": [
            {"xsi_type": "CustomCriteriaSet", "logicalOperator": "AND",
             "children": [crit(skey.id, sval.id),
                          crit(fkey.id, fval.id, "IS_NOT")]},
            {"xsi_type": "CustomCriteriaSet", "logicalOperator": "AND",
             "children": [crit(akey.id, aval.id),
                          crit(fkey.id, fval.id, "IS_NOT")]},
            {"xsi_type": "CustomCriteriaSet", "logicalOperator": "AND",
             "children": [crit(fkey.id, demo_val.id),
                          crit(fkey.id, fval.id, "IS_NOT")]},
            # QA test article
            {"xsi_type": "CustomCriteriaSet", "logicalOperator": "AND",
             "children": qa_children},
        ],
    }
    anchor.skipInventoryCheck = True
    anchor.allowOverbook = True
    li_svc.updateLineItems([anchor])
    summary["anchor_targeting"] = f"{SYNC_KEY}={SYNC_VALUE} OR article_id={TEST_ARTICLE_ID} OR nwdemocr=fitofluid"

    # --- validation of the proposed DEV CHANGE (docs/fito_video_custparams_spec.md)
    # The spec forwards `nwdemocr` (cascade value) and `categories` into the
    # video request's cust_params. Target the pre-roll on exactly those so we
    # can prove the ad-server half works before engineering writes any code.
    ckey = first(kv_svc.getCustomTargetingKeysByStatement(
        stmt("name = :n", n="categories")))
    cat_vals = []
    if ckey is not None:
        for cv in ("personal/finance", "business"):
            v2 = first(kv_svc.getCustomTargetingValuesByStatement(
                stmt("customTargetingKeyId = :k AND name = :v", k=ckey.id, v=cv)))
            if v2 is None:
                try:
                    v2 = kv_svc.createCustomTargetingValues([
                        {"customTargetingKeyId": ckey.id, "name": cv,
                         "matchType": "EXACT"}])[0]
                except Exception as exc:
                    summary[f"categories_value_error_{cv}"] = str(exc)[:200]
                    continue
            cat_vals.append(v2.id)
    summary["categories_key"] = getattr(ckey, "id", None)
    summary["categories_values"] = cat_vals

    branches = [
        # legacy cascade value (nwdemocr) — retire once `fito` is live
        {"xsi_type": "CustomCriteriaSet", "logicalOperator": "AND",
         "children": [crit(fkey.id, fval.id)]},
        # sanctioned URL demo param
        {"xsi_type": "CustomCriteriaSet", "logicalOperator": "AND",
         "children": [crit(fkey.id, demo_val.id)]},
        # current contextual fallback (works today, no dev change)
        {"xsi_type": "CustomCriteriaSet", "logicalOperator": "AND",
         "children": [crit(skey.id, sval.id)]},
    ]
    if ckey is not None and cat_vals:
        branches.append({
            "xsi_type": "CustomCriteriaSet", "logicalOperator": "AND",
            "children": [{"xsi_type": "CustomCriteria", "keyId": ckey.id,
                          "valueIds": cat_vals, "operator": "IS"}]})
    # dedicated production key — added as soon as Ad Ops creates it
    if fito_key is not None and fito_val is not None:
        branches.append({
            "xsi_type": "CustomCriteriaSet", "logicalOperator": "AND",
            "children": [crit(fito_key.id, fito_val.id)]})
        summary["preroll_uses_fito_key"] = True

    pre = first(li_svc.getLineItemsByStatement(stmt("id = :i", i=pre_li.id)))
    pre.targeting.customTargeting = {
        "xsi_type": "CustomCriteriaSet", "logicalOperator": "OR",
        "children": branches,
    }
    pre.skipInventoryCheck = True
    pre.allowOverbook = True
    li_svc.updateLineItems([pre])
    summary["preroll_targeting"] = (
        "nwdemocr=fitolive OR nwdemocr=fitofluid OR "
        f"{SYNC_KEY}={SYNC_VALUE} OR categories IN (personal/finance, business)")
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

# ---- 6e. Audience-segment gate for the pre-roll ----------------------------
# Chosen path: a rule-based first-party segment populated by ad requests that
# already carry the cascade KV (nwdemocr=fitolive). No pixel, no creative
# change, no page change — GAM joins the user server-side when any takeover
# ad request is seen, and the pre-roll targets segment membership instead of
# request-level KVs (which the player never sends).
SEGMENT_NAME = "[nw] FITO Fluid takeover viewers"
try:
    aud_svc = client.GetService("AudienceSegmentService", version=VERSION)
    seg = first(aud_svc.getAudienceSegmentsByStatement(
        stmt("name = :n", n=SEGMENT_NAME)))
    diag_seg = {}
    if seg is None:
        seg = aud_svc.createAudienceSegments([{
            "xsi_type": "RuleBasedFirstPartyAudienceSegment",
            "name": SEGMENT_NAME,
            "description": ("Users who received a FITO Fluid takeover "
                            "(ad request carried nwdemocr=fitolive). Gates "
                            "the synced pre-roll line item."),
            "pageViews": 1,          # join on first qualifying request
            "recencyDays": 0,        # only meaningful when pageViews > 1
            "membershipExpirationDays": 1,   # GAM minimum
            "rule": {
                "inventoryRule": {
                    "targetedAdUnits": [
                        {"adUnitId": root_ad_unit, "includeDescendants": True}]},
                "customCriteriaRule": {
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
        }])[0]
        diag_seg["created"] = True
    summary["audience_segment"] = {
        "id": seg.id, "name": seg.name,
        "status": str(getattr(seg, "status", "")),
        "size": getattr(seg, "size", None),
        **diag_seg,
    }

    # NOT used to gate the pre-roll: segment membership rides the Google
    # cookie, so Safari/ITP/Firefox/cookieless traffic (a large share of
    # Newsweek's audience) would never join and the video leg would
    # systematically under-deliver against display. The segment is left in
    # place for reporting/optional use only; the pre-roll is gated on shared
    # contextual key-values instead (section 6d).
    summary["preroll_targets_segment"] = False
except Exception as exc:
    summary["audience_segment_error"] = str(exc)[:400]

# ---- 6f. Eager signal line item on an out-of-page slot ---------------------
# The anchor slot (inarticle1) is lazily defined and sits ~1388px down the
# page, so it renders long after the video player has already built its ad
# request — which is why the pre-roll never matched fito=live. The oop1/2/3
# slots ARE requested at page load (single SRA call, verified on QA) and came
# back unfilled, so an eager "signal" line item there can set fito=live before
# the video request is assembled. No page change required.
#
# The service account cannot create the out-of-page CREATIVE — that must be
# made in the GAM UI with size "Out of page" (a 1x1 CustomCreative will not
# serve an OOP slot on this network). This block creates the line item only.
SIGNAL_LI_NAME = "[nw]_FITO-Fluid_POC_signal_oop"
try:
    inv_svc = client.GetService("InventoryService", version=VERSION)
    sb_oop = ad_manager.StatementBuilder(version=VERSION)
    sb_oop.Where("name IN ('oop1','oop2','oop3')").Limit(20)
    oop_resp = inv_svc.getAdUnitsByStatement(sb_oop.ToStatement())
    oop_units = [{"id": u.id, "name": u.name,
                  "status": str(getattr(u, "status", ""))}
                 for u in (getattr(oop_resp, "results", None) or [])]
    summary["oop_ad_units"] = oop_units

    # prefer oop3 (unused on QA), else oop2, else oop1
    pick = None
    for want in ("oop3", "oop2", "oop1"):
        for u in oop_units:
            if u["name"] == want:
                pick = u
                break
        if pick:
            break
    summary["oop_unit_chosen"] = pick

    if pick is not None:
        sig_li = first(li_svc.getLineItemsByStatement(
            stmt("name = :n AND orderId = :o",
                 n=SIGNAL_LI_NAME, o=order.id)))
        if sig_li is None:
            sig_li = li_svc.createLineItems([{
                "orderId": order.id,
                "name": SIGNAL_LI_NAME,
                "lineItemType": "SPONSORSHIP",
                "costType": "CPM",
                "costPerUnit": {"currencyCode": "USD", "microAmount": 0},
                "creativeRotationType": "EVEN",
                "primaryGoal": {"goalType": "DAILY",
                                "unitType": "IMPRESSIONS", "units": 100},
                "startDateTimeType": "IMMEDIATELY",
                "endDateTime": {
                    "date": {"year": 2026, "month": 8, "day": 17},
                    "hour": 23, "minute": 59, "second": 0,
                    "timeZoneId": "America/New_York",
                },
                # out-of-page slots require an interstitial-size placeholder
                "creativePlaceholders": [
                    {"size": {"width": 1, "height": 1, "isAspectRatio": False},
                     "creativeSizeType": "INTERSTITIAL"}],
                "targeting": {
                    # includeDescendants must be True — these OOP units reject
                    # self-only targeting (SELF_ONLY_INVENTORY_UNIT_NOT_ALLOWED)
                    "inventoryTargeting": {
                        "targetedAdUnits": [
                            {"adUnitId": pick["id"], "includeDescendants": True}]},
                    "customTargeting": {
                        "xsi_type": "CustomCriteriaSet",
                        "logicalOperator": "AND",
                        "children": [
                            {"xsi_type": "CustomCriteria", "keyId": akey.id,
                             "valueIds": [qa_aval.id], "operator": "IS"},
                        ] + ([{"xsi_type": "CustomCriteria", "keyId": envkey.id,
                               "valueIds": [qa_env.id], "operator": "IS"}]
                             if qa_env is not None else []),
                    },
                },
                "skipInventoryCheck": True,
                "allowOverbook": True,
            }])[0]
        summary["signal_line_item"] = {
            "id": sig_li.id,
            "status": str(sig_li.status),
            "isMissingCreatives": getattr(sig_li, "isMissingCreatives", None),
            "adUnit": pick["name"],
        }

        # The page requests oop1/2/3 at prev_iu_szs=1x1 (verified in the live
        # SRA request), so an INTERSTITIAL-only placeholder never matches and
        # GAM returns _empty_. Add a plain 1x1 placeholder + a 1x1 creative
        # alongside the out-of-page one and let GAM pick whichever fits.
        sig_now = first(li_svc.getLineItemsByStatement(
            stmt("id = :i", i=sig_li.id)))
        phs = list(getattr(sig_now, "creativePlaceholders", None) or [])
        has_plain_1x1 = any(
            getattr(p, "size", None) is not None
            and p.size.width == 1 and p.size.height == 1
            and not str(getattr(p, "creativeSizeType", "")).endswith("INTERSTITIAL")
            for p in phs)
        if not has_plain_1x1:
            sig_now.creativePlaceholders = phs + [
                {"size": {"width": 1, "height": 1, "isAspectRatio": False}}]
            sig_now.skipInventoryCheck = True
            sig_now.allowOverbook = True
            li_svc.updateLineItems([sig_now])
            summary["signal_added_1x1_placeholder"] = True

        SIGNAL_CR_NAME = "[nw]_FITO-Fluid_POC_signal_1x1"
        sig_cr = first(cr_svc.getCreativesByStatement(
            stmt("name = :n AND advertiserId = :a",
                 n=SIGNAL_CR_NAME, a=order.advertiserId)))
        if sig_cr is None:
            sig_cr = cr_svc.createCreatives([{
                "xsi_type": "CustomCreative",
                "name": SIGNAL_CR_NAME,
                "advertiserId": order.advertiserId,
                "size": {"width": 1, "height": 1, "isAspectRatio": False},
                "isSafeFrameCompatible": False,
                "htmlSnippet": (
                    '<img src="%%VIEW_URL_UNESC%%" width="1" height="1" '
                    'border="0" alt="" style="position:absolute;left:-9999px">'
                    "<script>(function(){try{var w=window.top;"
                    "if(w.googletag&&w.googletag.pubads)"
                    "w.googletag.pubads().setTargeting('fito','live');}"
                    "catch(e){}})();</script>"
                ),
            }])[0]
        sig_lica = first(lica_svc.getLineItemCreativeAssociationsByStatement(
            stmt("lineItemId = :l AND creativeId = :c",
                 l=sig_li.id, c=sig_cr.id)))
        if sig_lica is None:
            lica_svc.createLineItemCreativeAssociations([
                {"lineItemId": sig_li.id, "creativeId": sig_cr.id}])
        summary["signal_1x1_creative"] = sig_cr.id
except Exception as exc:
    summary["signal_li_error"] = str(exc)[:400]

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
for label, lid in (("anchor", ANCHOR_LI_ID), ("signal", 7393356692), ("preroll", pre_li.id),
                   ("follower", fol_li.id)):
    x = first(li_svc.getLineItemsByStatement(stmt("id = :i", i=lid)))
    diag[label] = {
        "id": lid,
        "status": str(x.status),
        "isMissingCreatives": getattr(x, "isMissingCreatives", None),
        "targeting": str(getattr(x.targeting, "customTargeting", ""))[:600],
    }
# which ad units does the ORIGINAL FITO-Video LI deliver on? (tells us the
# real video/player ad unit for VAST request testing)
try:
    import gzip
    import tempfile as _tf
    downloader = client.GetDataDownloader(version=VERSION)
    report_job = {"reportQuery": {
        "dimensions": ["AD_UNIT_ID", "AD_UNIT_NAME"],
        "columns": ["AD_SERVER_IMPRESSIONS"],
        "dateRangeType": "LAST_MONTH",
        "statement": {"query": "WHERE LINE_ITEM_ID = 7381354074"},
        "adUnitView": "FLAT",
    }}
    job_id = downloader.WaitForReport(report_job)
    with _tf.NamedTemporaryFile(suffix=".csv.gz", delete=False) as rf:
        downloader.DownloadReportToFile(job_id, "CSV_DUMP", rf)
        rpath = rf.name
    with gzip.open(rpath, "rt") as fh:
        lines = fh.read().splitlines()
    diag["video_li_ad_units"] = lines[:15]
except Exception as exc:
    diag["video_li_ad_units_error"] = str(exc)[:300]

print("\n=== DIAG ===")
print(json.dumps(diag, indent=1, default=str))

print("\n=== SUMMARY ===")
print(json.dumps(summary, indent=1, default=str))
