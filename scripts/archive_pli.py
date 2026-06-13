#!/usr/bin/env python
"""Archive one GAM Proposal Line Item (PD/PG/Sponsorship) by id.

Runs in CI (``.github/workflows/archive_pli.yml``), which the dashboard's
stale-deals "Archive" button dispatches via ``workflow_dispatch`` — so GAM
*write* credentials stay in GitHub Actions secrets instead of on the
read-only Streamlit dashboard. GAM creds come from ``GAM_SERVICE_ACCOUNT_JSON``
/ ``GAM_NETWORK_ID`` in the environment (set by the workflow from repo
secrets).

Usage:
    python scripts/archive_pli.py <pli_id> [deal_name]

Exits non-zero on failure so the Action run goes red and the failure email
fires; emits GitHub ``::error::`` / ``::notice::`` annotations so the reason
is visible in the run summary.
"""
from __future__ import annotations

import sys

from gam_client import GAMClient


def main() -> int:
    if len(sys.argv) < 2 or not sys.argv[1].strip():
        print("usage: archive_pli.py <pli_id> [deal_name]", file=sys.stderr)
        return 2
    pli_id = sys.argv[1].strip()
    deal = sys.argv[2].strip() if len(sys.argv) > 2 else ""
    label = pli_id + (f" ({deal})" if deal else "")
    print(f"Archiving proposal line item {label} …")
    try:
        GAMClient().archive_proposal_line_item(pli_id, raise_on_error=True)
    except Exception as e:  # noqa: BLE001 — surface the real reason to the run log
        print(f"::error::archive failed for PLI {label}: {type(e).__name__}: {e}")
        return 1
    print(f"::notice::Archived proposal line item {label}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
