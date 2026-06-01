"""Pull all GAM entities related to Nativo and surface recent changes.

Searches broadly for anything Nativo-related:
  - Orders with 'Nativo' OR 'Native' in the name
  - Line items with 'Nativo' OR 'Native' in the name
  - All line items in order 3648897741 (Newsweek_Test — the known Nativo parent order)

Reports current status + lastModifiedDateTime for every entity found,
sorted by most-recently-modified first. Entities changed since 2026-05-21
are flagged with ***.

GAM change history (who made each change) is not accessible via API.
For user attribution visit:
  https://admanager.google.com/22541732127#admin/changeHistory
"""

import os, json, tempfile, datetime
from googleads import ad_manager, oauth2

NETWORK_CODE        = os.environ["GAM_NETWORK_ID"]
KEY_JSON            = os.environ["GAM_SERVICE_ACCOUNT_JSON"]
API_VERSION         = "v202605"
AUDIT_START         = datetime.date(2026, 5, 21)
KNOWN_NATIVO_ORDER  = 3648897741   # Newsweek_Test — confirmed parent of the Nativo LI


def _setup_client():
    key_data = json.loads(KEY_JSON)
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(key_data, f)
        key_file = f.name
    oauth2_client = oauth2.GoogleServiceAccountClient(
        key_file, "https://www.googleapis.com/auth/dfp"
    )
    return ad_manager.AdManagerClient(
        oauth2_client, "NativoAudit/1.0", network_code=NETWORK_CODE
    )


def _paginate(svc, method_name, stmt_builder):
    rows = []
    method = getattr(svc, method_name)
    while True:
        resp = method(stmt_builder.ToStatement())
        if not hasattr(resp, "results") or not resp.results:
            break
        rows.extend(resp.results)
        stmt_builder.offset += stmt_builder.limit
        if stmt_builder.offset >= int(getattr(resp, "totalResultSetSize", 0)):
            break
    return rows


def _fmt_dt(soap_dt):
    if soap_dt is None:
        return "?"
    try:
        d = soap_dt.date
        return f"{d.year:04d}-{d.month:02d}-{d.day:02d}"
    except Exception:
        return str(soap_dt)


def _recent(soap_dt):
    s = _fmt_dt(soap_dt)
    try:
        return datetime.date.fromisoformat(s) >= AUDIT_START
    except Exception:
        return False


def _flag(soap_dt):
    return "  *** MODIFIED SINCE 2026-05-21 ***" if _recent(soap_dt) else ""


def _print_order(o):
    flag = _flag(getattr(o, "lastModifiedDateTime", None))
    print(f"  id={getattr(o,'id','?')}")
    print(f"    name    : {getattr(o,'name','?')}")
    print(f"    status  : {getattr(o,'status','?')}")
    print(f"    lastMod : {_fmt_dt(getattr(o,'lastModifiedDateTime',None))}{flag}")
    print()


def _print_li(li):
    flag    = _flag(getattr(li, "lastModifiedDateTime", None))
    goal    = getattr(getattr(li, "primaryGoal", None), "units", "?")
    start   = _fmt_dt(getattr(li, "startDateTime", None))
    end     = _fmt_dt(getattr(li, "endDateTime", None))
    print(f"  id={getattr(li,'id','?')}")
    print(f"    name    : {getattr(li,'name','?')}")
    print(f"    orderId : {getattr(li,'orderId','?')}")
    print(f"    type    : {getattr(li,'lineItemType','?')}")
    print(f"    status  : {getattr(li,'computedStatus', getattr(li,'status','?'))}")
    print(f"    goal    : {goal}")
    print(f"    flight  : {start} → {end}")
    print(f"    lastMod : {_fmt_dt(getattr(li,'lastModifiedDateTime',None))}{flag}")
    print()


def _by_name(svc_or_lis, method_name, terms, entity_label):
    """Query by LIKE for each term, deduplicate by id."""
    if isinstance(svc_or_lis, list):
        return svc_or_lis  # already fetched
    seen, results = set(), []
    for term in terms:
        sb = ad_manager.StatementBuilder(version=API_VERSION)
        sb.Where("name LIKE :q").WithBindVariable("q", f"%{term}%")
        try:
            rows = _paginate(svc_or_lis, method_name, sb)
            for r in rows:
                rid = getattr(r, "id", None)
                if rid not in seen:
                    seen.add(rid)
                    results.append(r)
        except Exception as exc:
            print(f"  [WARN] {entity_label} LIKE '%{term}%' failed: {exc}")
    return results


def main():
    client    = _setup_client()
    order_svc = client.GetService("OrderService",   version=API_VERSION)
    li_svc    = client.GetService("LineItemService", version=API_VERSION)

    print("=" * 72)
    print("GAM Nativo-related Entity Audit — all changes, all authors")
    print(f"Network     : {NETWORK_CODE}")
    print(f"Search terms: 'Nativo', 'Native'")
    print(f"Also pulls  : all LIs in order {KNOWN_NATIVO_ORDER} (Newsweek_Test)")
    print(f"Flagging    : lastModifiedDateTime >= {AUDIT_START}")
    print(f"Attribution : admanager.google.com/{NETWORK_CODE}#admin/changeHistory")
    print("=" * 72)

    # ── Orders: Nativo OR Native in name ─────────────────────────────────────
    print("\n── ORDERS (name contains 'Nativo' or 'Native') ──")
    orders = _by_name(order_svc, "getOrdersByStatement", ["Nativo", "Native"], "Order")
    if not orders:
        print("  None found.\n")
    else:
        orders_sorted = sorted(orders, key=lambda o: _fmt_dt(getattr(o, "lastModifiedDateTime", None)), reverse=True)
        recent_orders = [o for o in orders_sorted if _recent(getattr(o, "lastModifiedDateTime", None))]
        print(f"  {len(orders)} order(s) found  |  {len(recent_orders)} modified since {AUDIT_START}\n")
        for o in orders_sorted:
            _print_order(o)

    # ── Line items: Nativo OR Native in name ──────────────────────────────────
    print("── LINE ITEMS (name contains 'Nativo' or 'Native') ──")
    lis_by_name = _by_name(li_svc, "getLineItemsByStatement", ["Nativo", "Native"], "LineItem")
    if not lis_by_name:
        print("  None found.\n")
    else:
        lis_sorted  = sorted(lis_by_name, key=lambda l: _fmt_dt(getattr(l, "lastModifiedDateTime", None)), reverse=True)
        recent_lis  = [l for l in lis_sorted if _recent(getattr(l, "lastModifiedDateTime", None))]
        print(f"  {len(lis_by_name)} line item(s) found  |  {len(recent_lis)} modified since {AUDIT_START}\n")
        for li in lis_sorted:
            _print_li(li)

    # ── All LIs in Newsweek_Test order (3648897741) ───────────────────────────
    print(f"── ALL LINE ITEMS in order {KNOWN_NATIVO_ORDER} (Newsweek_Test) ──")
    sb = ad_manager.StatementBuilder(version=API_VERSION)
    sb.Where("orderId = :oid").WithBindVariable("oid", int(KNOWN_NATIVO_ORDER))
    try:
        order_lis = _paginate(li_svc, "getLineItemsByStatement", sb)
        # deduplicate against already-shown LIs
        shown_ids   = {getattr(l, "id") for l in lis_by_name}
        extra_lis   = [l for l in order_lis if getattr(l, "id") not in shown_ids]
        all_order   = sorted(order_lis, key=lambda l: _fmt_dt(getattr(l, "lastModifiedDateTime", None)), reverse=True)
        recent_ord  = [l for l in all_order if _recent(getattr(l, "lastModifiedDateTime", None))]
        print(f"  {len(order_lis)} total LI(s) in this order  |  {len(recent_ord)} modified since {AUDIT_START}\n")
        for li in all_order:
            _print_li(li)
    except Exception as exc:
        print(f"  ERROR: {exc}")

    print("=" * 72)
    print("Done. For who made each change:")
    print(f"  https://admanager.google.com/{NETWORK_CODE}#admin/changeHistory")
    print("=" * 72)


if __name__ == "__main__":
    main()
