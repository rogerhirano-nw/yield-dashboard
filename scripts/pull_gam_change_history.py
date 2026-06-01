"""One-off: pull every GAM change made by the service account since 2026-05-21.

Primary method: PublisherQueryLanguageService + ChangeHistory PQL table.
  - Fetches ALL changes network-wide since AUDIT_START
  - Highlights rows attributed to the API / service account

Supplemental: entity snapshots via OrderService, LineItemService,
ProposalLineItemService for the specific IDs Claude-assisted code targeted,
so we can see current state even if PQL history is unavailable.

Service account under audit:
  gam-reports@newsweek-ad-manager-reports.iam.gserviceaccount.com
"""

import os, json, sys, tempfile, datetime, re
from googleads import ad_manager, oauth2

NETWORK_CODE   = os.environ["GAM_NETWORK_ID"]
KEY_JSON       = os.environ["GAM_SERVICE_ACCOUNT_JSON"]
API_VERSION    = "v202605"
AUDIT_START    = "2026-05-21T00:00:00"
AUDIT_START_DT = datetime.date(2026, 5, 21)
SVC_ACCOUNT    = "gam-reports@newsweek-ad-manager-reports.iam.gserviceaccount.com"

# Specific entity IDs from code review
ORDER_IDS   = [4057788230, 4068491190]
LI_IDS      = [7306352098]


# ── client setup ─────────────────────────────────────────────────────────────

def _setup_client():
    key_data = json.loads(KEY_JSON)
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(key_data, f)
        key_file = f.name
    oauth2_client = oauth2.GoogleServiceAccountClient(
        key_file, "https://www.googleapis.com/auth/dfp"
    )
    return ad_manager.AdManagerClient(
        oauth2_client, "ChangeHistoryAudit/1.0", network_code=NETWORK_CODE
    )


# ── PQL helpers ───────────────────────────────────────────────────────────────

def _pql_value(v):
    """Extract a plain Python value from a PQL typed Value object."""
    if v is None:
        return None
    xtype = getattr(v, "_xsi_type", None) or str(type(v))
    val   = getattr(v, "value", None)
    if val is None:
        return None
    # DateTimeValue wraps a DateTime SOAP object
    if "DateTime" in str(xtype) and hasattr(val, "date"):
        d = val.date
        return f"{d.year:04d}-{d.month:02d}-{d.day:02d}T{getattr(val,'hour',0):02d}:{getattr(val,'minute',0):02d}:{getattr(val,'second',0):02d}"
    return str(val)


def _pql_rows_to_dicts(result_set):
    if not hasattr(result_set, "columnTypes") or not result_set.columnTypes:
        return []
    cols = [c.labelName for c in result_set.columnTypes]
    out  = []
    rows = getattr(result_set, "rows", None) or []
    for row in rows:
        vals = getattr(row, "values", []) or []
        out.append(dict(zip(cols, [_pql_value(v) for v in vals])))
    return out


def _pql_paginate(pql_svc, base_query):
    """Paginate a PQL SELECT, collecting all rows into a list of dicts."""
    all_rows = []
    offset   = 0
    limit    = 500
    while True:
        q = f"{base_query} LIMIT {limit} OFFSET {offset}"
        try:
            rs = pql_svc.select({"query": q})
        except Exception as exc:
            print(f"  [PQL ERROR] {exc}", file=sys.stderr)
            break
        batch = _pql_rows_to_dicts(rs)
        all_rows.extend(batch)
        if len(batch) < limit:
            break
        offset += limit
    return all_rows


# ── SOAP entity helpers ───────────────────────────────────────────────────────

def _soap_paginate(svc, method_name, stmt_builder):
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


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    client = _setup_client()

    # ── Section 1: PQL ChangeHistory (network-wide) ───────────────────────────
    print("=" * 72)
    print("SECTION 1 — PQL ChangeHistory (all changes since 2026-05-21)")
    print("=" * 72)

    pql_svc = client.GetService("PublisherQueryLanguageService", version=API_VERSION)

    # Try progressively simpler queries to find what the API supports.
    # GAM PQL ChangeHistory notes:
    #  - T-separator in DateTime literals is rejected; use space separator
    #  - ORDER BY is not supported on ChangeHistory
    #  - If ChangeHistory table is unavailable, fall back to SELECT * probe
    AUDIT_START_PQL = AUDIT_START.replace("T", " ")  # '2026-05-21 00:00:00'

    candidate_queries = [
        # Full query, space-separated datetime, no ORDER BY
        (
            "SELECT Id, DateTime, EntityId, EntityType, EntityName, "
            "ChangeType, UserId, UserName, Application "
            "FROM ChangeHistory "
            f"WHERE DateTime >= '{AUDIT_START_PQL}'"
        ),
        # Minimal column set in case some columns don't exist
        (
            "SELECT Id, DateTime, EntityId, EntityType, ChangeType, Application "
            "FROM ChangeHistory "
            f"WHERE DateTime >= '{AUDIT_START_PQL}'"
        ),
        # No filter — table existence probe
        "SELECT Id, DateTime, EntityId, EntityType, ChangeType, Application FROM ChangeHistory",
        # Wildcard probe
        "SELECT * FROM ChangeHistory",
    ]

    ch_rows = []
    for attempt, base_query in enumerate(candidate_queries, 1):
        print(f"  [attempt {attempt}] {base_query[:80]} …", flush=True)
        rows = _pql_paginate(pql_svc, base_query)
        if rows:
            ch_rows = rows
            print(f"  → {len(rows)} row(s) returned")
            break
        print(f"  → no rows (PQL error or empty table)")

    print(f"Fetching ChangeHistory rows (>= {AUDIT_START}) …", flush=True)

    if not ch_rows:
        print("  No rows returned — ChangeHistory PQL table may be unavailable or empty.")
    else:
        print(f"  {len(ch_rows)} total change(s) since {AUDIT_START}\n")

        # Separate API/service-account rows from UI rows
        api_rows  = [r for r in ch_rows if (r.get("Application") or "").upper() in ("API", "BATCH")]
        ui_rows   = [r for r in ch_rows if r not in api_rows]

        if api_rows:
            print(f"  ── API-originated changes ({len(api_rows)}) ──")
            for r in api_rows:
                marker = "*** SVC_ACCT ***" if SVC_ACCOUNT in (r.get("UserName") or "") else ""
                print(
                    f"  {r.get('DateTime','?')[:19]}  "
                    f"[{(r.get('EntityType') or '?'):20s}] "
                    f"id={r.get('EntityId','?'):15s}  "
                    f"{(r.get('ChangeType') or '?'):18s}  "
                    f"user={r.get('UserName','?')}  "
                    f"app={r.get('Application','?')}  "
                    f"{marker}"
                )
        else:
            print("  No API-originated changes found.")

        print()
        if ui_rows:
            print(f"  ── UI-originated changes ({len(ui_rows)}) ──")
            for r in ui_rows:
                print(
                    f"  {r.get('DateTime','?')[:19]}  "
                    f"[{(r.get('EntityType') or '?'):20s}] "
                    f"id={r.get('EntityId','?'):15s}  "
                    f"{(r.get('ChangeType') or '?'):18s}  "
                    f"user={r.get('UserName','?')}  "
                    f"app={r.get('Application','?')}"
                )
        else:
            print("  No UI-originated changes found.")

    # ── Section 2: Entity snapshots ───────────────────────────────────────────
    print()
    print("=" * 72)
    print("SECTION 2 — Current entity state for Claude-targeted IDs")
    print("=" * 72)

    # Orders
    order_svc = client.GetService("OrderService", version=API_VERSION)
    for oid in ORDER_IDS:
        sb = ad_manager.StatementBuilder(version=API_VERSION)
        sb.Where("id = :id").WithBindVariable("id", int(oid))
        try:
            orders = _soap_paginate(order_svc, "getOrdersByStatement", sb)
            if orders:
                o = orders[0]
                print(f"\nORDER {oid}")
                print(f"  name             : {getattr(o, 'name', '?')}")
                print(f"  status           : {getattr(o, 'status', '?')}")
                print(f"  lastModifiedDT   : {_fmt_dt(getattr(o, 'lastModifiedDateTime', None))}")
            else:
                print(f"\nORDER {oid}: not found")
        except Exception as exc:
            print(f"\nORDER {oid}: ERROR — {exc}")

    # Line items in those orders + specific LI IDs
    li_svc = client.GetService("LineItemService", version=API_VERSION)

    for oid in ORDER_IDS:
        sb = ad_manager.StatementBuilder(version=API_VERSION)
        sb.Where("orderId = :oid").WithBindVariable("oid", int(oid))
        try:
            lis = _soap_paginate(li_svc, "getLineItemsByStatement", sb)
            print(f"\nLINE ITEMS in order {oid} ({len(lis)} total)")
            for li in lis:
                goal  = getattr(getattr(li, "primaryGoal", None), "units", "?")
                print(
                    f"  id={getattr(li,'id','?'):15}  "
                    f"status={str(getattr(li,'computedStatus',getattr(li,'status','?'))):15}  "
                    f"goal={goal}  "
                    f"lastMod={_fmt_dt(getattr(li,'lastModifiedDateTime',None))}  "
                    f"name={getattr(li,'name','?')}"
                )
        except Exception as exc:
            print(f"\nLINE ITEMS in order {oid}: ERROR — {exc}")

    # Specific LI IDs not already covered
    for lid in LI_IDS:
        sb = ad_manager.StatementBuilder(version=API_VERSION)
        sb.Where("id = :id").WithBindVariable("id", int(lid))
        try:
            lis = _soap_paginate(li_svc, "getLineItemsByStatement", sb)
            if lis:
                li   = lis[0]
                goal = getattr(getattr(li, "primaryGoal", None), "units", "?")
                print(f"\nLINE ITEM {lid}")
                print(f"  name             : {getattr(li, 'name', '?')}")
                print(f"  status           : {getattr(li, 'computedStatus', getattr(li, 'status', '?'))}")
                print(f"  primaryGoal.units: {goal}")
                print(f"  lastModifiedDT   : {_fmt_dt(getattr(li, 'lastModifiedDateTime', None))}")
        except Exception as exc:
            print(f"\nLINE ITEM {lid}: ERROR — {exc}")

    # Proposal line items — look for any archived since audit start
    pli_svc = client.GetService("ProposalLineItemService", version=API_VERSION)
    sb = ad_manager.StatementBuilder(version=API_VERSION)
    sb.Where("status = :s").WithBindVariable("s", "ARCHIVED")
    try:
        plis = _soap_paginate(pli_svc, "getProposalLineItemsByStatement", sb)
        recent = [
            p for p in plis
            if _fmt_dt(getattr(p, "lastModifiedDateTime", None)) >= AUDIT_START[:10]
        ]
        print(f"\nPROPOSAL LINE ITEMS — archived (status=ARCHIVED, lastMod >= {AUDIT_START[:10]})")
        if recent:
            for p in recent:
                print(
                    f"  id={getattr(p,'id','?'):15}  "
                    f"lastMod={_fmt_dt(getattr(p,'lastModifiedDateTime',None))}  "
                    f"name={getattr(p,'name','?')}"
                )
        else:
            print("  None found — no PLIs were archived via the dashboard in this window.")
    except Exception as exc:
        print(f"\nPROPOSAL LINE ITEMS: ERROR — {exc}")

    print()
    print("=" * 72)
    print(f"Audit complete. Service account: {SVC_ACCOUNT}")
    print("Section 1 shows ALL network-wide API changes; Section 2 confirms")
    print("current state of specific IDs targeted by Claude-assisted code.")
    print("=" * 72)


if __name__ == "__main__":
    main()
