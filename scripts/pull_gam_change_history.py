"""GAM entity-state audit for Claude-touched entities (2026-05-21 → today).

GAM does not expose change history via the SOAP or REST API — neither
ChangeHistoryService nor a ChangeHistory PQL table exist.  For user-level
attribution (who triggered each API call), check the GAM Admin UI:
  https://admanager.google.com/22541732127#admin/changeHistory

This script instead pulls the current state of every entity that Claude-
assisted code could have written to, answering:
  - Were LIs in order 4057788230 renamed? (tmp_rename_li workflow)
  - Were test LIs created in order 4068491190? (betting_test_lis_batch --apply)
  - Was the control LI 7306352098 goal modified?
  - Were any Proposal Line Items archived via the dashboard button?

All answers come from the live GAM API (OrderService, LineItemService,
ProposalLineItemService) using the service account in env.

Service account under audit:
  gam-reports@newsweek-ad-manager-reports.iam.gserviceaccount.com
"""

import os, json, sys, tempfile, datetime
from googleads import ad_manager, oauth2

NETWORK_CODE   = os.environ["GAM_NETWORK_ID"]
KEY_JSON       = os.environ["GAM_SERVICE_ACCOUNT_JSON"]
API_VERSION    = "v202605"
AUDIT_START    = datetime.date(2026, 5, 21)
SVC_ACCOUNT    = "gam-reports@newsweek-ad-manager-reports.iam.gserviceaccount.com"

# Specific IDs from code review
ORDER_IDS       = [4057788230, 4068491190]
SPECIFIC_LI_IDS = [7306352098]

# Expected state from the scripts (for comparison)
RENAME_ORDER_ID     = 4057788230   # tmp_rename_li.yml: THearn → THern
BETTING_ORDER_ID    = 4068491190   # betting_test_lis_batch.py --apply
CONTROL_LI_ID       = 7306352098   # goal: 1,875,000 → 1,230,000 (per script)
EXPECTED_TEST_LIS   = 3            # OnlineCasino, Basketball, SBEnthusiast


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


def _sep(label=""):
    print(f"\n{'─'*72}")
    if label:
        print(f"  {label}")
        print(f"{'─'*72}")


def main():
    client   = _setup_client()
    findings = []   # (label, result, detail)

    print("=" * 72)
    print("GAM Entity-State Audit")
    print(f"Service account : {SVC_ACCOUNT}")
    print(f"Audit window    : {AUDIT_START} → {datetime.date.today()}")
    print(f"NOTE: GAM does not expose change history via API.")
    print(f"      For user attribution visit:")
    print(f"      https://admanager.google.com/22541732127#admin/changeHistory")
    print("=" * 72)

    order_svc = client.GetService("OrderService", version=API_VERSION)
    li_svc    = client.GetService("LineItemService", version=API_VERSION)
    pli_svc   = client.GetService("ProposalLineItemService", version=API_VERSION)

    # ── CHECK 1: LI rename (order 4057788230, THearn → THern) ────────────────
    _sep("CHECK 1 — tmp_rename_li.yml: was THearn → THern rename applied?")
    sb = ad_manager.StatementBuilder(version=API_VERSION)
    sb.Where("orderId = :oid").WithBindVariable("oid", RENAME_ORDER_ID)
    try:
        lis = _paginate(li_svc, "getLineItemsByStatement", sb)
        bad  = [li for li in lis if "THearn" in (getattr(li, "name", "") or "")]
        good = [li for li in lis if "THern"  in (getattr(li, "name", "") or "") and "THearn" not in (getattr(li, "name", "") or "")]
        print(f"  Total LIs in order {RENAME_ORDER_ID}: {len(lis)}")
        print(f"  LIs still with 'THearn' (rename NOT applied): {len(bad)}")
        print(f"  LIs with 'THern'  (rename applied):           {len(good)}")
        if bad:
            print("  STILL WRONG:")
            for li in bad:
                print(f"    id={getattr(li,'id','?')}  name={getattr(li,'name','?')}")
        result = "RENAME APPLIED — all THern, none THearn" if not bad else f"PARTIAL/INCOMPLETE — {len(bad)} still have THearn"
        findings.append(("tmp_rename_li.yml rename", result, f"{len(good)}/{len(lis)} LIs renamed"))
    except Exception as exc:
        print(f"  ERROR: {exc}")
        findings.append(("tmp_rename_li.yml rename", "ERROR", str(exc)))

    # ── CHECK 2: Betting test LIs created (order 4068491190) ─────────────────
    _sep("CHECK 2 — betting_test_lis_batch.py: were test LIs created?")
    sb = ad_manager.StatementBuilder(version=API_VERSION)
    sb.Where("orderId = :oid").WithBindVariable("oid", BETTING_ORDER_ID)
    try:
        lis = _paginate(li_svc, "getLineItemsByStatement", sb)
        print(f"  Total LIs in order {BETTING_ORDER_ID}: {len(lis)}")
        for li in lis:
            goal = getattr(getattr(li, "primaryGoal", None), "units", "?")
            print(
                f"  id={getattr(li,'id','?'):15}  "
                f"status={str(getattr(li,'computedStatus', getattr(li,'status','?'))):15}  "
                f"goal={goal}  "
                f"lastMod={_fmt_dt(getattr(li,'lastModifiedDateTime',None))}  "
                f"name={getattr(li,'name','?')}"
            )
        # The script was meant to create 3 audience-segment LIs beyond the 1 control LI
        test_lis = [li for li in lis if getattr(li, "id", 0) != CONTROL_LI_ID]
        if len(test_lis) >= EXPECTED_TEST_LIS:
            result = f"TEST LIs CREATED — {len(test_lis)} non-control LIs exist (expected {EXPECTED_TEST_LIS})"
        elif len(test_lis) > 0:
            result = f"PARTIAL — {len(test_lis)} test LIs (expected {EXPECTED_TEST_LIS})"
        else:
            result = "NOT RUN — only control LI exists"
        findings.append(("betting_test_lis_batch --apply", result, f"{len(lis)} total LIs in order"))
    except Exception as exc:
        print(f"  ERROR: {exc}")
        findings.append(("betting_test_lis_batch --apply", "ERROR", str(exc)))

    # ── CHECK 3: Control LI goal ──────────────────────────────────────────────
    _sep(f"CHECK 3 — Control LI {CONTROL_LI_ID}: was the goal reduced?")
    sb = ad_manager.StatementBuilder(version=API_VERSION)
    sb.Where("id = :id").WithBindVariable("id", CONTROL_LI_ID)
    try:
        lis = _paginate(li_svc, "getLineItemsByStatement", sb)
        if lis:
            li   = lis[0]
            goal = getattr(getattr(li, "primaryGoal", None), "units", None)
            print(f"  name             : {getattr(li, 'name', '?')}")
            print(f"  status           : {getattr(li, 'computedStatus', getattr(li,'status','?'))}")
            print(f"  primaryGoal.units: {goal}  (original=1875000, script target=1230000)")
            print(f"  lastModifiedDT   : {_fmt_dt(getattr(li, 'lastModifiedDateTime', None))}")
            if goal is not None and int(goal) != 1875000:
                result = f"GOAL MODIFIED — current={goal} (was 1875000)"
            elif goal is not None:
                result = "GOAL UNCHANGED — still at original 1875000"
            else:
                result = "GOAL UNKNOWN"
            findings.append(("Control LI goal modification", result, f"primaryGoal.units={goal}"))
        else:
            print(f"  LI {CONTROL_LI_ID} not found")
            findings.append(("Control LI goal modification", "NOT FOUND", ""))
    except Exception as exc:
        print(f"  ERROR: {exc}")
        findings.append(("Control LI goal modification", "ERROR", str(exc)))

    # ── CHECK 4: PLI archives via dashboard ───────────────────────────────────
    _sep("CHECK 4 — archive_proposal_line_item(): were any PLIs archived via dashboard?")
    sb = ad_manager.StatementBuilder(version=API_VERSION)
    sb.Where("status = :s").WithBindVariable("s", "ARCHIVED")
    try:
        plis = _paginate(pli_svc, "getProposalLineItemsByStatement", sb)
        recent = [
            p for p in plis
            if _fmt_dt(getattr(p, "lastModifiedDateTime", None)) >= str(AUDIT_START)
        ]
        print(f"  Total archived PLIs: {len(plis)}")
        print(f"  Archived since {AUDIT_START}: {len(recent)}")
        if recent:
            for p in recent:
                print(
                    f"  id={getattr(p,'id','?'):15}  "
                    f"lastMod={_fmt_dt(getattr(p,'lastModifiedDateTime',None))}  "
                    f"name={getattr(p,'name','?')}"
                )
            result = f"PLIs ARCHIVED — {len(recent)} archived since {AUDIT_START}"
        else:
            result = "NEVER TRIGGERED — no PLIs archived in audit window"
        findings.append(("Dashboard archive_proposal_line_item()", result, f"{len(recent)} recent archives"))
    except Exception as exc:
        print(f"  ERROR: {exc}")
        findings.append(("Dashboard archive_proposal_line_item()", "ERROR", str(exc)))

    # ── Summary ───────────────────────────────────────────────────────────────
    print()
    print("=" * 72)
    print("AUDIT SUMMARY")
    print("=" * 72)
    for label, result, detail in findings:
        print(f"\n  [{label}]")
        print(f"    Result : {result}")
        if detail:
            print(f"    Detail : {detail}")

    print()
    print("For user-level API attribution (who/when each call was made):")
    print("  https://admanager.google.com/22541732127#admin/changeHistory")
    print("=" * 72)


if __name__ == "__main__":
    main()
