"""One-off read-only GAM pull: what does the new FITO KVP targeting look like?

Context: a KVP-targeted FITO placement was pushed 2026-08-06 and does not
work on the new-template article pages (atpl_ver=next). This dumps, via
SOAP (read-only get* calls only):

  1. All ACTIVE custom targeting keys (is there a `fito` key at all?).
  2. Values on the `fito` key (if it exists) + on `article_id` matching the
     test article (12280782).
  3. Line items named *FITO* (any casing), newest-modified first: status,
     priority, sizes, inventory targeting, and the full custom targeting
     tree with key/value ids resolved to names.
  4. The creatives on the most recently modified FITO LIs — name, size,
     SafeFrame flag, and the head of the htmlSnippet (to read the DOM
     anchor selectors the wrapper uses).

Run from the one-off workflow .github/workflows/inspect_fito_kvp.yml
(needs GAM_SERVICE_ACCOUNT_JSON + GAM_NETWORK_ID).
"""

import json
import os
import sys
import tempfile
import warnings

warnings.filterwarnings("ignore")

TEST_ARTICLE_ID = "12280782"
V = "v202605"


def soap_client():
    from googleads import ad_manager, oauth2

    sa = json.loads(os.environ["GAM_SERVICE_ACCOUNT_JSON"])
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(sa, f)
        keyfile = f.name
    oc = oauth2.GoogleServiceAccountClient(keyfile, "https://www.googleapis.com/auth/dfp")
    client = ad_manager.AdManagerClient(
        oc, "NewsweekDashboard/1.0", network_code=os.environ["GAM_NETWORK_ID"]
    )
    return client, ad_manager


def rows(resp):
    return list(getattr(resp, "results", None) or [])


def fmt_dt(dt):
    try:
        d = dt["date"]
        parts = [f"{int(d['year']):04d}-{int(d['month']):02d}-{int(d['day']):02d}"]
        hour = getattr(dt, "hour", None)
        minute = getattr(dt, "minute", None)
        if hour is not None:
            parts.append(f"{int(hour):02d}:{int(minute or 0):02d}")
        tz = getattr(dt, "timeZoneId", "")
        if tz:
            parts.append(str(tz))
        return " ".join(parts)
    except Exception:
        return str(dt)


def main():
    client, ad_manager = soap_client()
    ct_svc = client.GetService("CustomTargetingService", version=V)
    li_svc = client.GetService("LineItemService", version=V)
    lica_svc = client.GetService("LineItemCreativeAssociationService", version=V)
    cr_svc = client.GetService("CreativeService", version=V)

    # ── 1. all active custom targeting keys ─────────────────────────────────
    print("=" * 72)
    print("ACTIVE CUSTOM TARGETING KEYS")
    print("=" * 72)
    sb = ad_manager.StatementBuilder(version=V)
    sb.Where("status = 'ACTIVE'").Limit(500)
    keys = rows(ct_svc.getCustomTargetingKeysByStatement(sb.ToStatement()))
    key_by_id = {}
    for k in sorted(keys, key=lambda k: str(k["name"]).lower()):
        key_by_id[int(k["id"])] = str(k["name"])
        print(f"  {k['id']:>14}  {k['name']:<24} type={k['type']:<10} display={getattr(k, 'displayName', '') or ''}")
    print(f"  ({len(keys)} keys)")

    def key_ids_named(*names):
        low = {n.lower() for n in names}
        return [kid for kid, n in key_by_id.items() if n.lower() in low]

    # ── 2. values on fito-ish and article_id keys ───────────────────────────
    for label, kids, name_filter in [
        ("VALUES ON `fito`-LIKE KEYS", [k for k, n in key_by_id.items() if "fito" in n.lower()], None),
        (f"`article_id`-LIKE VALUES MATCHING {TEST_ARTICLE_ID}",
         key_ids_named("article_id", "articleid", "article"), TEST_ARTICLE_ID),
    ]:
        print()
        print("=" * 72)
        print(label)
        print("=" * 72)
        if not kids:
            print("  (no matching key exists)")
            continue
        for kid in kids:
            sb = ad_manager.StatementBuilder(version=V)
            if name_filter:
                sb.Where("customTargetingKeyId = :kid AND name LIKE :n")
                sb.WithBindVariable("n", f"%{name_filter}%")
            else:
                sb.Where("customTargetingKeyId = :kid")
            sb.WithBindVariable("kid", kid).Limit(200)
            vals = rows(ct_svc.getCustomTargetingValuesByStatement(sb.ToStatement()))
            print(f"  key {key_by_id[kid]!r} (id={kid}): {len(vals)} value(s)")
            for v in vals[:50]:
                print(f"    value id={v['id']}  name={v['name']!r}  display={getattr(v, 'displayName', '') or ''!r}  status={getattr(v, 'status', '?')}")

    # ── 3. FITO line items, newest first ────────────────────────────────────
    print()
    print("=" * 72)
    print("LINE ITEMS NAMED *FITO* (newest modified first)")
    print("=" * 72)
    seen, lis = set(), []
    for pat in ("%FITO%", "%Fito%", "%fito%"):
        sb = ad_manager.StatementBuilder(version=V)
        sb.Where("name LIKE :n").WithBindVariable("n", pat).Limit(100)
        for li in rows(li_svc.getLineItemsByStatement(sb.ToStatement())):
            if int(li["id"]) not in seen:
                seen.add(int(li["id"]))
                lis.append(li)

    def last_mod(li):
        try:
            d = li["lastModifiedDateTime"]["date"]
            return (d["year"], d["month"], d["day"], li["lastModifiedDateTime"]["hour"])
        except Exception:
            return (0, 0, 0, 0)

    lis.sort(key=last_mod, reverse=True)

    value_name_cache = {}

    def value_name(vid):
        if vid in value_name_cache:
            return value_name_cache[vid]
        sb = ad_manager.StatementBuilder(version=V)
        sb.Where("id = :v").WithBindVariable("v", vid).Limit(1)
        vs = rows(ct_svc.getCustomTargetingValuesByStatement(sb.ToStatement()))
        value_name_cache[vid] = str(vs[0]["name"]) if vs else f"<unknown {vid}>"
        return value_name_cache[vid]

    def render_criteria(node, depth=2):
        pad = " " * (depth * 2)
        xt = node.get("xsi_type") if hasattr(node, "get") else getattr(node, "xsi_type", "")
        if "Set" in str(type(node)) or (hasattr(node, "logicalOperator") and getattr(node, "logicalOperator", None)):
            print(f"{pad}{getattr(node, 'logicalOperator', '?')} (")
            for ch in getattr(node, "children", None) or []:
                render_criteria(ch, depth + 1)
            print(f"{pad})")
        elif hasattr(node, "keyId"):
            kid = int(node["keyId"])
            vnames = [value_name(int(v)) for v in (getattr(node, "valueIds", None) or [])]
            print(f"{pad}{key_by_id.get(kid, kid)} {node['operator']} {vnames}")
        else:
            print(f"{pad}{node}")

    for li in lis[:10]:
        print(f"\n  LI {li['id']}  {li['name']!r}")
        print(f"     status={li['status']}  type={li['lineItemType']}  priority={getattr(li, 'priority', '?')}  order={li['orderId']}")
        print(f"     lastModified={fmt_dt(li['lastModifiedDateTime'])}")
        sizes = [f"{p['size']['width']}x{p['size']['height']}" for p in (getattr(li, "creativePlaceholders", None) or [])]
        print(f"     sizes={sizes}")
        inv = getattr(li["targeting"], "inventoryTargeting", None)
        if inv is not None:
            units = [str(u["adUnitId"]) for u in (getattr(inv, "targetedAdUnits", None) or [])]
            print(f"     adUnits={units}")
        ct = getattr(li["targeting"], "customTargeting", None)
        if ct is not None:
            print("     customTargeting:")
            render_criteria(ct, depth=4)
        else:
            print("     customTargeting: (none)")

    # ── 3b. recently modified LIs that reference the fito key or the test
    #        article's article_id value (the LI pushed today may not carry
    #        "FITO" in its name) ─────────────────────────────────────────────
    print()
    print("=" * 72)
    print("RECENTLY MODIFIED LIs REFERENCING `fito` OR article_id=12280782")
    print("=" * 72)
    fito_key_ids = {k for k, n in key_by_id.items() if "fito" in n.lower()}
    article_key_ids = set(key_ids_named("article_id", "articleid"))

    def collect_ids(node, kids, vids):
        if node is None:
            return
        if hasattr(node, "keyId"):
            kids.add(int(node["keyId"]))
            for v in getattr(node, "valueIds", None) or []:
                vids.add(int(v))
        for ch in getattr(node, "children", None) or []:
            collect_ids(ch, kids, vids)

    # resolve the article_id value ids matching the test article
    article_value_ids = set()
    for kid in article_key_ids:
        sb = ad_manager.StatementBuilder(version=V)
        sb.Where("customTargetingKeyId = :kid AND name = :n")
        sb.WithBindVariable("kid", kid).WithBindVariable("n", TEST_ARTICLE_ID).Limit(10)
        for v in rows(ct_svc.getCustomTargetingValuesByStatement(sb.ToStatement())):
            article_value_ids.add(int(v["id"]))

    sb = ad_manager.StatementBuilder(version=V)
    sb.Where("isArchived = false").Limit(80)
    sb.OrderBy("lastModifiedDateTime", ascending=False)
    recent = rows(li_svc.getLineItemsByStatement(sb.ToStatement()))
    print(f"  scanned {len(recent)} most recently modified line items:")
    for li in recent[:25]:
        print(f"    {fmt_dt(li['lastModifiedDateTime'])}  LI {li['id']}  status={li['status']:<22} {str(li['name'])[:70]!r}")

    matches = []
    for li in recent:
        kids, vids = set(), set()
        collect_ids(getattr(li["targeting"], "customTargeting", None), kids, vids)
        if (kids & fito_key_ids) or (vids & article_value_ids):
            matches.append(li)
    print(f"\n  {len(matches)} of them target fito / article_id={TEST_ARTICLE_ID}:")
    for li in matches:
        print(f"\n  LI {li['id']}  {li['name']!r}")
        print(f"     status={li['status']}  type={li['lineItemType']}  priority={getattr(li, 'priority', '?')}  order={li['orderId']}")
        print(f"     roadblocking={getattr(li, 'roadblockingType', '?')}  rotation={getattr(li, 'creativeRotationType', '?')}  deliveryRate={getattr(li, 'deliveryRateType', '?')}")
        print(f"     lastModified={fmt_dt(li['lastModifiedDateTime'])}")
        sizes = [f"{p['size']['width']}x{p['size']['height']}" for p in (getattr(li, "creativePlaceholders", None) or [])]
        print(f"     sizes={sizes}")
        inv = getattr(li["targeting"], "inventoryTargeting", None)
        if inv is not None:
            units = [str(u["adUnitId"]) for u in (getattr(inv, "targetedAdUnits", None) or [])]
            print(f"     adUnits={units}")
        ct = getattr(li["targeting"], "customTargeting", None)
        if ct is not None:
            print("     customTargeting:")
            render_criteria(ct, depth=4)
    # make their creatives print in section 4 too
    lis = matches + lis

    # ── 3c. resolve the ad units + delivery stats for the matched LIs ───────
    print()
    print("=" * 72)
    print("AD UNITS + DELIVERY ON THE MATCHED LIs")
    print("=" * 72)
    inv_svc = client.GetService("InventoryService", version=V)
    unit_ids = set()
    for li in matches:
        inv = getattr(li["targeting"], "inventoryTargeting", None)
        if inv is not None:
            for u in getattr(inv, "targetedAdUnits", None) or []:
                unit_ids.add(str(u["adUnitId"]))
    for uid in sorted(unit_ids):
        sb = ad_manager.StatementBuilder(version=V)
        sb.Where("id = :u").WithBindVariable("u", int(uid)).Limit(1)
        us = rows(inv_svc.getAdUnitsByStatement(sb.ToStatement()))
        if us:
            u = us[0]
            parent = getattr(u, "parentPath", None) or []
            path = "/".join(str(p["adUnitCode"]) for p in parent) + "/" + str(u["adUnitCode"])
            print(f"  unit {uid}: code={u['adUnitCode']!r}  path={path}  status={getattr(u, 'status', '?')}")
        else:
            print(f"  unit {uid}: <not found>")
    for li in matches:
        st = getattr(li, "stats", None)
        imps = getattr(st, "impressionsDelivered", None) if st is not None else None
        clicks = getattr(st, "clicksDelivered", None) if st is not None else None
        start = fmt_dt(getattr(li, "startDateTime", None)) if getattr(li, "startDateTime", None) else "?"
        end = "unlimited" if getattr(li, "unlimitedEndDateTime", False) else (
            fmt_dt(getattr(li, "endDateTime", None)) if getattr(li, "endDateTime", None) else "?")
        print(f"  LI {li['id']} {str(li['name'])[:48]!r}: impressions={imps} clicks={clicks} start={start} end={end}")

    # ── 4. creatives on the 4 newest FITO LIs ───────────────────────────────
    print()
    print("=" * 72)
    print("CREATIVES ON NEWEST FITO LIs")
    print("=" * 72)
    for li in lis[:4]:
        sb = ad_manager.StatementBuilder(version=V)
        sb.Where("lineItemId = :li").WithBindVariable("li", int(li["id"])).Limit(20)
        licas = rows(lica_svc.getLineItemCreativeAssociationsByStatement(sb.ToStatement()))
        print(f"\n  LI {li['id']} {li['name']!r}: {len(licas)} creative association(s)")
        for lica in licas:
            cid = int(lica["creativeId"])
            sb = ad_manager.StatementBuilder(version=V)
            sb.Where("id = :c").WithBindVariable("c", cid).Limit(1)
            crs = rows(cr_svc.getCreativesByStatement(sb.ToStatement()))
            if not crs:
                print(f"    creative {cid}: <not readable>")
                continue
            cr = crs[0]
            size = getattr(cr, "size", None)
            sz = f"{size['width']}x{size['height']}" if size is not None else "?"
            sf = getattr(cr, "isSafeFrameCompatible", "?")
            print(f"    creative {cid}  {cr['name']!r}  size={sz}  safeFrame={sf}  lica_status={lica['status']}")
            snippet = getattr(cr, "htmlSnippet", None) or getattr(cr, "snippet", None) or ""
            if snippet:
                head = str(snippet)[:1200].replace("\n", "\n      | ")
                print(f"      | {head}")

    print("\nDone (read-only).")


if __name__ == "__main__":
    main()
