"""One-shot DB check: row counts, date ranges, and sample line_item_names
for dv_attention and dv_ivt, cross-referenced against gam_campaigns."""
import os, sqlalchemy, re
url = os.environ["DATABASE_URL"]
engine = sqlalchemy.create_engine(url)
with engine.connect() as conn:
    for tbl in ("dv_attention", "dv_ivt"):
        try:
            n = conn.execute(sqlalchemy.text(f'SELECT COUNT(*) FROM "{tbl}"')).scalar()
            r = conn.execute(sqlalchemy.text(f'SELECT MIN(date), MAX(date) FROM "{tbl}"')).fetchone()
            cols = [c["name"] for c in sqlalchemy.inspect(conn).get_columns(tbl)]
            print(f"\n{tbl}: {n} rows | {r[0]} → {r[1]} | cols: {cols}")
            rows = conn.execute(sqlalchemy.text(
                f'SELECT DISTINCT line_item_name FROM "{tbl}" LIMIT 6'
            )).fetchall()
            for row in rows:
                print(f"  DV line_item_name: {row[0]!r}")
        except Exception as e:
            print(f"\n{tbl}: ERROR — {e}")

    # GAM line items for comparison
    try:
        rows = conn.execute(sqlalchemy.text(
            "SELECT DISTINCT line_item_name FROM gam_campaigns "
            "WHERE end_date_time >= NOW() - INTERVAL '30 days' LIMIT 6"
        )).fetchall()
        print("\ngam_campaigns recent line_item_names (strip leading #N ):")
        for row in rows:
            cleaned = re.sub(r"^#\d+\s+", "", str(row[0]))
            print(f"  GAM: {row[0]!r}  ->  cleaned: {cleaned!r}")
    except Exception as e:
        print(f"\ngam_campaigns: ERROR — {e}")
