"""Locate gam_campaigns rows whose 8th underscore token (the "advertiser"
slot extracted by dashboard.py around line 2597) doesn't look like a real
advertiser name — currently chasing why "$6" appears in the Advertiser
filter dropdown.

Reads $DATABASE_URL. Prints to stdout; an Actions workflow tees the output
to a PR comment.
"""

from __future__ import annotations

import os
import sys

import sqlalchemy
from sqlalchemy import text


def main() -> int:
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("DATABASE_URL not set", file=sys.stderr)
        return 2

    engine = sqlalchemy.create_engine(url)
    with engine.connect() as conn:
        print("=== Suspicious advertiser-slot tokens (starts with $ or pure digits) ===")
        rows = conn.execute(text(
            """
            SELECT split_part(line_item_name, '_', 8) AS adv_token,
                   COUNT(*)                          AS n_rows,
                   COUNT(DISTINCT line_item_id)      AS n_lis
            FROM gam_campaigns
            WHERE split_part(line_item_name, '_', 8) ~ '^(\\$|\\d)'
            GROUP BY adv_token
            ORDER BY adv_token
            """
        )).fetchall()
        if not rows:
            print("  (none)")
        for r in rows:
            print(f"  token={r[0]!r:<20}  rows={r[1]:<6}  lis={r[2]}")

        print()
        print("=== Line items with adv-slot == '$6' ===")
        rows = conn.execute(text(
            """
            SELECT DISTINCT
                   line_item_id,
                   COALESCE(status, '')      AS status,
                   COALESCE(order_name, '')  AS order_name,
                   line_item_name
            FROM gam_campaigns
            WHERE split_part(line_item_name, '_', 8) = '$6'
            ORDER BY line_item_name
            """
        )).fetchall()
        if not rows:
            print("  (none — already cleaned up?)")
        for r in rows:
            print(f"  li={r[0]}  status={r[1]}")
            print(f"    order: {r[2]}")
            print(f"    name : {r[3]}")

        print()
        print("=== Row count + most recent _pulled_at for those LIs ===")
        rows = conn.execute(text(
            """
            SELECT line_item_id,
                   COUNT(*)              AS row_count,
                   MIN(report_start)     AS first_seen,
                   MAX(report_start)     AS last_seen,
                   MAX(_pulled_at)       AS last_pulled
            FROM gam_campaigns
            WHERE split_part(line_item_name, '_', 8) = '$6'
            GROUP BY line_item_id
            ORDER BY last_seen DESC
            """
        )).fetchall()
        for r in rows:
            print(f"  li={r[0]}  rows={r[1]}  first={r[2]}  last={r[3]}  pulled={r[4]}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
