"""Diagnostic: check PA deal classification in gam_campaigns."""
import os, sqlalchemy, pandas as pd

engine = sqlalchemy.create_engine(os.environ["DATABASE_URL"])

with engine.connect() as conn:
    df_types = pd.read_sql(
        sqlalchemy.text("""
            SELECT line_item_type, COUNT(*) as n
            FROM gam_campaigns
            GROUP BY line_item_type
            ORDER BY n DESC
        """), conn)
    print("=== line_item_type breakdown ===")
    print(df_types.to_string(index=False))

    # How many PA-named orders exist?
    df_pa = pd.read_sql(
        sqlalchemy.text("""
            SELECT line_item_type, order_name, COUNT(*) as n
            FROM gam_campaigns
            WHERE order_name LIKE 'Newsweek_PA_%%'
            GROUP BY line_item_type, order_name
            ORDER BY n DESC
            LIMIT 40
        """), conn)
    print("\n=== Newsweek_PA_ orders (all types) ===")
    print(df_pa.to_string(index=False) if not df_pa.empty else "NONE FOUND")

    # What do the 1996 PRICE_PRIORITY rows look like?
    df_pp = pd.read_sql(
        sqlalchemy.text("""
            SELECT DISTINCT order_name
            FROM gam_campaigns
            WHERE line_item_type = 'PRICE_PRIORITY'
              AND order_name NOT LIKE 'Newsweek_Direct%%'
              AND order_name NOT LIKE 'Newsweek_Test%%'
            ORDER BY order_name
            LIMIT 40
        """), conn)
    print("\n=== PRICE_PRIORITY non-Direct order names ===")
    print(df_pp.to_string(index=False) if not df_pp.empty else "NONE")
