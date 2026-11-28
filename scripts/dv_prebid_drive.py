#!/usr/bin/env python3
"""Read DoubleVerify's monthly pre-bid counts from their shared Google Drive folder.

DV drops one CSV per month into the "TAG Certification [Client Shared Folder]"
Drive folder (`report_YYYY-MM.csv`, columns `Day,Total`, one row per day,
uploaded around the 1st). The month's sum is the TAG deck's **DV pre-bid**
figure — verified 2026-11-28: September's 30 rows sum to 50,519,576, exactly
the number in the deck as sent.

Reads with the GAM service account (`GAM_SERVICE_ACCOUNT_JSON`, Drive
read-only scope). **The folder must be shared with that service account's
email** (GAM → Admin → Global settings → Network settings → API access lists
it), and the Drive API must be enabled in its Google Cloud project; until then
this logs why and the deck still builds with whatever is typed in.

Called by `build_tag_ivt_deck.py` before it builds; also runnable alone:
  python scripts/dv_prebid_drive.py [--dry-run]
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import sys
from datetime import date

FOLDER_ID = os.environ.get("DV_DRIVE_FOLDER_ID", "1JXnbW3dBa-KjEO_W-aWQXCzUdApxmtxt")
API = "https://www.googleapis.com/drive/v3/files"
NAME_RE = re.compile(r"^report_(\d{4})-(\d{2})\.csv$")


def month_total(text: str, year: int, month: int) -> tuple[int, int]:
    """(sum of Total, number of days) for rows dated in year-month."""
    tot = days = 0
    for row in csv.DictReader(io.StringIO(text.lstrip("﻿"))):
        d = (row.get("Day") or "").strip()
        v = (row.get("Total") or "").replace(",", "").strip()
        if not d or not v:
            continue
        y, m, _ = d.split("-")
        if int(y) == year and int(m) == month:
            tot += int(float(v))
            days += 1
    return tot, days


def _session():
    import requests
    from google.auth.transport.requests import Request
    from google.oauth2 import service_account
    info = json.loads(os.environ["GAM_SERVICE_ACCOUNT_JSON"])
    cred = service_account.Credentials.from_service_account_info(
        info, scopes=["https://www.googleapis.com/auth/drive.readonly"])
    cred.refresh(Request())
    s = requests.Session()
    s.headers["Authorization"] = f"Bearer {cred.token}"
    return s


def fetch() -> dict[tuple[int, int], tuple[int, int]]:
    """{(year, month): (dv_prebid, days)} for every report CSV in the folder."""
    s = _session()
    common = {"supportsAllDrives": "true", "includeItemsFromAllDrives": "true"}
    r = s.get(API, params={**common, "q": f"'{FOLDER_ID}' in parents and trashed = false",
                           "fields": "files(id,name,modifiedTime)", "pageSize": 200}, timeout=30)
    if r.status_code >= 300:
        raise RuntimeError(f"Drive list returned {r.status_code}: {r.text[:300]}")
    files = r.json().get("files", [])
    if not files:
        raise RuntimeError("the DV folder lists no files — is it shared with the GAM service "
                           "account (GAM → Admin → Network settings → API access shows its email)?")
    out = {}
    for f in files:
        m = NAME_RE.match(f["name"])
        if not m:
            continue
        y, mo = int(m.group(1)), int(m.group(2))
        c = s.get(f"{API}/{f['id']}", params={**common, "alt": "media"}, timeout=60)
        c.raise_for_status()
        out[(y, mo)] = month_total(c.content.decode("utf-8", "replace"), y, mo)
    return out


def apply(engine, found: dict) -> list[str]:
    """Write dv_prebid into tag_ivt_months (other columns untouched). Only full
    months (every day present) are written. Returns lines for the log."""
    import calendar
    import sqlalchemy as sa
    log = []
    with engine.begin() as c:
        for (y, m), (tot, days) in sorted(found.items()):
            need = calendar.monthrange(y, m)[1]
            if days < need:
                log.append(f"{y}-{m:02d}: {days}/{need} days in DV's file — skipped until complete")
                continue
            c.execute(sa.text(
                "INSERT INTO tag_ivt_months (month, dv_prebid) VALUES (:d, :v) "
                "ON CONFLICT (month) DO UPDATE SET dv_prebid = EXCLUDED.dv_prebid, "
                "updated_at = CURRENT_TIMESTAMP"), {"d": date(y, m, 1), "v": tot})
            log.append(f"{y}-{m:02d}: DV pre-bid {tot:,} ({days} days)")
    return log


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    found = fetch()
    if a.dry_run:
        for (y, m), (tot, days) in sorted(found.items()):
            print(f"{y}-{m:02d}: {tot:,} over {days} days")
        return 0
    sys.path.insert(0, os.path.dirname(__file__))
    import build_tag_ivt_deck as bt
    for line in apply(bt.engine(), found):
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
