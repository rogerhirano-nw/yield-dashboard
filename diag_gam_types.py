"""Diagnostic: print line_item_type distribution and PA order name samples from gam_campaigns."""
import os, sqlalchemy, pandas as pd

engine = sqlalchemy.create_engine(os.environ["DATABASE_URL"])

with engine.connect() as conn:
    # line_item_type breakdown
    df_types = pd.read_sql("""
        SELECT line_item_type, COUNT(*) as n
        FROM gam_campaigns
        GROUP BY line_item_type
        ORDER BY n DESC
    """, conn)
    print("=== line_item_type breakdown ===")
    print(df_types.to_string(index=False))

    # Sample non-Direct, non-Test order names (to see PA naming)
    df_orders = pd.read_sql("""
        SELECT DISTINCT line_item_type, order_name
        FROM gam_campaigns
        WHERE order_name NOT LIKE 'Newsweek_Direct%'
          AND order_name NOT LIKE 'Newsweek_Test%'
        ORDER BY line_item_type, order_name
        LIMIT 60
    """, conn)
    print("\n=== Non-Direct order names by line_item_type ===")
    print(df_orders.to_string(index=False))
