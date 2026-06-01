"""One-off: pull GAM ChangeHistoryService for every entity Claude touched.

Scope (2026-05-21 → today):
  ORDER        4057788230   — tmp_rename_li workflow (LI name typo fix)
  ORDER        4068491190   — betting CPA order (test LIs batch)
  LINE_ITEM    7306352098   — betting control LI (goal adjustment)
  PROPOSAL_LINE_ITEM (all)  — any dashboard-triggered archives via
                              archive_proposal_line_item()

Output is plain text, sorted by date, ready to paste into a Jira/Slack
thread or read from the Actions log.
"""

import os, json, sys, tempfile, datetime, textwrap
from googleads import ad_manager, oauth2

NETWORK_CODE  = os.environ["GAM_NETWORK_ID"]
KEY_JSON      = os.environ["GAM_SERVICE_ACCOUNT_JSON"]
API_VERSION   = "v202605"
AUDIT_START   = datetime.date(2026, 5, 21)   # first Claude-touched commit

# Entities with known IDs
SPECIFIC_ENTITIES = [
    ("ORDER",     4057788230),   # order targeted by tmp_rename_li
    ("ORDER",     4068491190),   # betting CPA order
    ("LINE_ITEM", 7306352098),   # betting control LI
]

# Broad sweep — all PLI changes (catches any dashboard-triggered archives)
BROAD_ENTITY_TYPES = ["PROPOSAL_LINE_ITEM"]


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


def _paginate(svc, stmt_builder):
    rows = []
    while True:
        resp = svc.getChangeHistorysByStatement(stmt_builder.ToStatement())
        if not hasattr(resp, "results") or not resp.results:
            break
        rows.extend(resp.results)
        stmt_builder.offset += stmt_builder.limit
        if stmt_builder.offset >= int(getattr(resp, "totalResultSetSize", 0)):
            break
    return rows


def _query_specific(svc, entity_type, entity_id):
    sb = ad_manager.StatementBuilder(version=API_VERSION)
    sb.Where("entityType = :et AND entityId = :eid")
    sb.WithBindVariable("et",  entity_type)
    sb.WithBindVariable("eid", int(entity_id))
    try:
        return _paginate(svc, sb)
    except Exception as exc:
        print(f"  [WARN] query {entity_type}/{entity_id} failed: {exc}", file=sys.stderr)
        return []


def _query_by_type(svc, entity_type):
    sb = ad_manager.StatementBuilder(version=API_VERSION)
    sb.Where("entityType = :et")
    sb.WithBindVariable("et", entity_type)
    try:
        return _paginate(svc, sb)
    except Exception as exc:
        print(f"  [WARN] query {entity_type} failed: {exc}", file=sys.stderr)
        return []


def _parse_dt(ch):
    """Extract a comparable datetime.date from a ChangeHistory record."""
    raw = getattr(ch, "dateTime", None) or getattr(ch, "changeDateTime", None)
    if raw is None:
        return datetime.date.min
    try:
        # SOAP DateTime object has date.year / date.month / date.day attrs
        d = raw.date
        return datetime.date(int(d.year), int(d.month), int(d.day))
    except Exception:
        return datetime.date.min


def _fmt_row(ch):
    dt      = _parse_dt(ch)
    etype   = str(getattr(ch, "entityType", "?"))
    eid     = str(getattr(ch, "entityId",   "?"))
    ctype   = str(getattr(ch, "changeType", "?"))
    app     = str(getattr(ch, "application","?"))
    user    = getattr(ch, "createdByUser", None)
    user_s  = f"{getattr(user,'name','?')} <{getattr(user,'email','?')}>" if user else "?"
    cid     = str(getattr(ch, "id",        "?"))
    return (dt, f"{dt}  [{etype:25s}] id={eid:15s}  {ctype:20s}  app={app:12s}  user={user_s}  changeId={cid}")


def main():
    client = _setup_client()
    svc    = client.GetService("ChangeHistoryService", version=API_VERSION)

    all_rows = []

    print("=== Querying specific entities ===")
    for etype, eid in SPECIFIC_ENTITIES:
        print(f"  {etype} {eid} ...", end=" ", flush=True)
        rows = _query_specific(svc, etype, eid)
        print(f"{len(rows)} record(s)")
        all_rows.extend(rows)

    print("\n=== Broad sweep by entity type ===")
    for etype in BROAD_ENTITY_TYPES:
        print(f"  {etype} (all) ...", end=" ", flush=True)
        rows = _query_by_type(svc, etype)
        print(f"{len(rows)} record(s)")
        all_rows.extend(rows)

    # De-duplicate by changeId, then filter to audit window
    seen = set()
    deduped = []
    for ch in all_rows:
        cid = str(getattr(ch, "id", id(ch)))
        if cid not in seen:
            seen.add(cid)
            if _parse_dt(ch) >= AUDIT_START:
                deduped.append(ch)

    deduped.sort(key=_parse_dt)

    print(f"\n{'='*80}")
    print(f"GAM Change History — {AUDIT_START} → today  ({len(deduped)} record(s))")
    print(f"{'='*80}")

    if not deduped:
        print("No changes found in the audit window.")
        return

    prev_date = None
    for ch in deduped:
        dt, line = _fmt_row(ch)
        if dt != prev_date:
            print(f"\n--- {dt} ---")
            prev_date = dt
        print(f"  {line}")

    print(f"\n{'='*80}")
    print("Legend: app=API → programmatic call  |  app=UI → user action in GAM interface")
    print(f"Entities audited: ORDER {[e[1] for e in SPECIFIC_ENTITIES if e[0]=='ORDER']}, "
          f"LINE_ITEM {[e[1] for e in SPECIFIC_ENTITIES if e[0]=='LINE_ITEM']}, "
          f"+ all PROPOSAL_LINE_ITEM records")


if __name__ == "__main__":
    main()
