"""One-off: pull current state of all Nativo orders and line items from GAM.

Queries OrderService and LineItemService for any entity whose name contains
'Nativo' (case-insensitive via LIKE). Reports current status, goal, and
lastModifiedDateTime so any recent changes can be spotted.

GAM change history is not accessible via API; for user-level attribution visit:
  https://admanager.google.com/22541732127#admin/changeHistory
"""

import os, json, sys, tempfile, datetime
from googleads import ad_manager, oauth2

NETWORK_CODE = os.environ["GAM_NETWORK_ID"]
KEY_JSON     = os.environ["GAM_SERVICE_ACCOUNT_JSON"]
API_VERSION  = "v202605"
AUDIT_START  = datetime.date(2026, 5, 21)


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


def _recent(soap_dt, since=AUDIT_START):
    s = _fmt_dt(soap_dt)
    if s == "?":
        return False
    try:
        return datetime.date.fromisoformat(s) >= since
    except Exception:
        return False


def main():
    client    = _setup_client()
    order_svc = client.GetService("OrderService",   version=API_VERSION)
    li_svc    = client.GetService("LineItemService", version=API_VERSION)

    print("=" * 72)
    print("GAM Nativo Entity Audit")
    print(f"Network  : {NETWORK_CODE}")
    print(f"Flagging : lastModifiedDateTime >= {AUDIT_START}")
    print(f"NOTE: For user attribution visit:")
    print(f"      https://admanager.google.com/22541732127#admin/changeHistory")
    print("=" * 72)

    # ── Orders ────────────────────────────────────────────────────────────────
    print("\n── ORDERS containing 'Nativo' ──")
    sb = ad_manager.StatementBuilder(version=API_VERSION)
    sb.Where("name LIKE :q").WithBindVariable("q", "%Nativo%")
    try:
        orders = _paginate(order_svc, "getOrdersByStatement", sb)
        if not orders:
            print("  None found.")
        else:
            print(f"  {len(orders)} order(s) found\n")
            for o in orders:
                flag = "  *** MODIFIED SINCE 2026-05-21 ***" if _recent(getattr(o, "lastModifiedDateTime", None)) else ""
                print(f"  id={getattr(o,'id','?')}")
                print(f"    name       : {getattr(o,'name','?')}")
                print(f"    status     : {getattr(o,'status','?')}")
                print(f"    lastMod    : {_fmt_dt(getattr(o,'lastModifiedDateTime',None))}{flag}")
                print()
    except Exception as exc:
        print(f"  ERROR: {exc}")

    # ── Line Items ────────────────────────────────────────────────────────────
    print("── LINE ITEMS containing 'Nativo' ──")
    sb = ad_manager.StatementBuilder(version=API_VERSION)
    sb.Where("name LIKE :q").WithBindVariable("q", "%Nativo%")
    try:
        lis = _paginate(li_svc, "getLineItemsByStatement", sb)
        if not lis:
            print("  None found.")
        else:
            recent = [li for li in lis if _recent(getattr(li, "lastModifiedDateTime", None))]
            print(f"  {len(lis)} line item(s) found  |  {len(recent)} modified since {AUDIT_START}\n")

            # Print recently-modified first, then the rest
            for li in sorted(lis, key=lambda x: _fmt_dt(getattr(x, "lastModifiedDateTime", None)), reverse=True):
                flag = "  *** MODIFIED SINCE 2026-05-21 ***" if _recent(getattr(li, "lastModifiedDateTime", None)) else ""
                goal = getattr(getattr(li, "primaryGoal", None), "units", "?")
                print(f"  id={getattr(li,'id','?')}")
                print(f"    name    : {getattr(li,'name','?')}")
                print(f"    status  : {getattr(li,'computedStatus', getattr(li,'status','?'))}")
                print(f"    goal    : {goal}")
                print(f"    lastMod : {_fmt_dt(getattr(li,'lastModifiedDateTime',None))}{flag}")
                print()
    except Exception as exc:
        print(f"  ERROR: {exc}")

    print("=" * 72)
    print("Done.")


if __name__ == "__main__":
    main()
