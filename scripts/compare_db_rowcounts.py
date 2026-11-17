"""Compare row counts of every public table between two Postgres databases.

Used after copying the Supabase cache to Azure Database for PostgreSQL:
    python scripts/compare_db_rowcounts.py "$SOURCE_URL" "$TARGET_URL"
Exits non-zero if any table is missing on the target or its count differs.
Read-only on both sides.
"""
import sys

import sqlalchemy
from sqlalchemy import text

SQL = """
SELECT c.relname FROM pg_class c
WHERE c.relnamespace = 'public'::regnamespace AND c.relkind = 'r'
ORDER BY 1
"""


def counts(url: str) -> dict[str, int]:
    eng = sqlalchemy.create_engine(url, connect_args={"connect_timeout": 15})
    with eng.connect() as conn:
        tables = [r for (r,) in conn.execute(text(SQL))]
        return {t: conn.execute(text(f'SELECT count(*) FROM public."{t}"')).scalar()
                for t in tables}


def main(src_url: str, dst_url: str) -> int:
    src, dst = counts(src_url), counts(dst_url)
    bad = 0
    print(f"{'table':<40} {'source':>12} {'target':>12}")
    for t in sorted(src):
        d = dst.get(t)
        flag = "" if d == src[t] else "  <-- MISMATCH" if d is not None else "  <-- MISSING"
        bad += bool(flag)
        print(f"{t:<40} {src[t]:>12,} {('—' if d is None else f'{d:,}'):>12}{flag}")
    extra = sorted(set(dst) - set(src))
    if extra:
        print("only on target:", ", ".join(extra))
    print(f"\n{len(src)} tables, {sum(src.values()):,} rows on source; "
          f"{bad} problem(s).")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
