"""Fix stale DV line item names for Infiniti: SO01104 → correct IO-style name.

Context: GAM line items were renamed around 2026-06-01. DV Pinnacle kept
reporting the old SO01104 name for a few days, leaving rows that can't join
to gam_campaigns. This script patches those rows in-place.

Mapping (verified against gam_campaigns 2026-06-08):
  MANV-Sponsorship Display lines: SO01104 → IO1104-6
  Sponsorship-Promotion Video lines: SO01104 → IO1104-7

Run: python scripts/fix_dv_infiniti_names.py [--dry-run]
"""

from __future__ import annotations

import os
import sys

import sqlalchemy
from sqlalchemy import text


DRY_RUN = "--dry-run" in sys.argv


def _update(conn, table: str, pattern: str, old_token: str, new_token: str) -> int:
    old = f"_{old_token}_"
    new = f"_{new_token}_"
    if DRY_RUN:
        n = conn.execute(text(
            f"SELECT COUNT(*) FROM {table} WHERE line_item_name LIKE :p AND line_item_name LIKE :o"
        ), {"p": f"%{pattern}%", "o": f"%{old}%"}).scalar()
        print(f"  [dry-run] {table}: would update {n} rows  "
              f"({old_token} → {new_token}  filter={pattern!r})")
        return n
    result = conn.execute(text(
        f"""
        UPDATE {table}
        SET line_item_name = REPLACE(line_item_name, :old, :new)
        WHERE line_item_name LIKE :p
          AND line_item_name LIKE :o
        """
    ), {"old": old, "new": new, "p": f"%{pattern}%", "o": f"%{old}%"})
    n = result.rowcount
    print(f"  {table}: updated {n} rows  ({old_token} → {new_token}  filter={pattern!r})")
    return n


def main() -> int:
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("DATABASE_URL not set", file=sys.stderr)
        return 2

    engine = sqlalchemy.create_engine(url)
    total = 0
    with engine.begin() as conn:
        print(f"=== Fix DV Infiniti stale names {'[DRY RUN]' if DRY_RUN else '[LIVE]'} ===")
        print()

        print("--- MANV Display: SO01104 → IO1104-6 ---")
        total += _update(conn, "dv_attention", "MANV-Sponsorship", "SO01104", "IO1104-6")
        total += _update(conn, "dv_ivt",       "MANV-Sponsorship", "SO01104", "IO1104-6")

        print()
        print("--- Sponsorship-Promotion Video: SO01104 → IO1104-7 ---")
        total += _update(conn, "dv_attention", "_US_Video_", "SO01104", "IO1104-7")
        total += _update(conn, "dv_ivt",       "_US_Video_", "SO01104", "IO1104-7")

        print()
        print(f"Total rows {'to update' if DRY_RUN else 'updated'}: {total}")

        if not DRY_RUN and total > 0:
            print()
            print("=== Remaining unmatched DV Infiniti names (should be empty) ===")
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
                print("  (none — all DV Infiniti names now match GAM)")
            for r in rows:
                print(f"  STILL UNMATCHED: {r[0]}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
