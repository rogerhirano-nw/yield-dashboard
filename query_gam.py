import os, sqlalchemy

engine = sqlalchemy.create_engine(os.environ["DATABASE_URL"])
with engine.connect() as conn:
    print("=== programmatic_channel ===")
    for row in conn.execute(sqlalchemy.text(
        "SELECT programmatic_channel, COUNT(*) n FROM gam_campaigns GROUP BY 1 ORDER BY 2 DESC"
    )):
        print(dict(row._mapping))

    print("\n=== line_item_type ===")
    for row in conn.execute(sqlalchemy.text(
        "SELECT line_item_type, COUNT(*) n FROM gam_campaigns GROUP BY 1 ORDER BY 2 DESC"
    )):
        print(dict(row._mapping))

    print("\n=== non-OpenExchange order names (sample) ===")
    for row in conn.execute(sqlalchemy.text(
        "SELECT DISTINCT order_name, line_item_type, programmatic_channel FROM gam_campaigns WHERE order_name NOT LIKE '%OpenExchange%' LIMIT 20"
    )):
        print(dict(row._mapping))
