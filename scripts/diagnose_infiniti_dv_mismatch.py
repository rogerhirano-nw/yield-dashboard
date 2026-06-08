"""Show the exact line_item_name values stored for Infiniti in gam_campaigns,
dv_attention, and dv_ivt so we can identify the DV↔GAM name mismatch.

Reads $DATABASE_URL. Output is posted as a PR comment by the companion workflow.
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

        print("=== gam_campaigns — distinct line_item_names containing 'infiniti' ===")
        rows = conn.execute(text(
            """
            SELECT DISTINCT line_item_name, status
            FROM gam_campaigns
            WHERE lower(line_item_name) LIKE '%infiniti%'
            ORDER BY line_item_name
            """
        )).fetchall()
        if not rows:
            print("  (none)")
        for r in rows:
            print(f"  [{r[1]}]  {r[0]}")

        print()
        print("=== dv_attention — distinct line_item_names containing 'infiniti' (last 14 days) ===")
        rows = conn.execute(text(
            """
            SELECT DISTINCT line_item_name,
                   MIN(date) AS first_date,
                   MAX(date) AS last_date,
                   COUNT(*)  AS rows
            FROM dv_attention
            WHERE lower(line_item_name) LIKE '%infiniti%'
              AND date >= CURRENT_DATE - INTERVAL '14 days'
            GROUP BY line_item_name
            ORDER BY line_item_name
            """
        )).fetchall()
        if not rows:
            print("  (none in last 14 days)")
        for r in rows:
            print(f"  {r[0]}")
            print(f"    dates {r[1]} → {r[2]}  ({r[3]} rows)")

        print()
        print("=== dv_ivt — distinct line_item_names containing 'infiniti' (last 14 days) ===")
        rows = conn.execute(text(
            """
            SELECT DISTINCT line_item_name,
                   MIN(date) AS first_date,
                   MAX(date) AS last_date,
                   COUNT(*)  AS rows
            FROM dv_ivt
            WHERE lower(line_item_name) LIKE '%infiniti%'
              AND date >= CURRENT_DATE - INTERVAL '14 days'
            GROUP BY line_item_name
            ORDER BY line_item_name
            """
        )).fetchall()
        if not rows:
            print("  (none in last 14 days)")
        for r in rows:
            print(f"  {r[0]}")
            print(f"    dates {r[1]} → {r[2]}  ({r[3]} rows)")

        print()
        print("=== Cross-check: DV names NOT found in gam_campaigns (last 14 days) ===")
        rows = conn.execute(text(
            """
            WITH dv_names AS (
                SELECT DISTINCT line_item_name FROM dv_attention
                WHERE lower(line_item_name) LIKE '%infiniti%'
                  AND date >= CURRENT_DATE - INTERVAL '14 days'
                UNION
                SELECT DISTINCT line_item_name FROM dv_ivt
                WHERE lower(line_item_name) LIKE '%infiniti%'
                  AND date >= CURRENT_DATE - INTERVAL '14 days'
            ),
            gam_names AS (
                SELECT DISTINCT line_item_name FROM gam_campaigns
                WHERE lower(line_item_name) LIKE '%infiniti%'
            )
            SELECT d.line_item_name
            FROM dv_names d
            LEFT JOIN gam_names g USING (line_item_name)
            WHERE g.line_item_name IS NULL
            ORDER BY d.line_item_name
            """
        )).fetchall()
        if not rows:
            print("  (all DV Infiniti names match GAM — no mismatch!)")
        for r in rows:
            print(f"  UNMATCHED in GAM: {r[0]}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
