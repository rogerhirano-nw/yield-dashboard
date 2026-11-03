#!/usr/bin/env python3
"""Read-only QA dump of every non-archived line item on a GAM order: order
header, then the full inspect_line_item.py setup (targeting, placeholders,
creatives, trackers, click-throughs) for each line, flagging the ones created
on a given date (default: today, America/New_York).

Usage:
    python3 scripts/qa_order.py 4236300211 [--created YYYY-MM-DD]
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

from inspect_line_item import _client, _dt, _q, inspect, V


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("order", type=int)
    ap.add_argument("--created", default=datetime.now(ZoneInfo("America/New_York")).date().isoformat())
    ap.add_argument("--all", action="store_true", help="full dump of every line, not just new ones")
    a = ap.parse_args()
    client = _client()
    o_svc = client.GetService("OrderService", version=V)
    li_svc = client.GetService("LineItemService", version=V)
    co_svc = client.GetService("CompanyService", version=V)
    order = _q(o_svc, "getOrdersByStatement", "id = :i", i=a.order)[0]
    adv = _q(co_svc, "getCompaniesByStatement", "id = :i", i=order.advertiserId)
    print(f"ORDER {order.id}: {order.name}")
    print(f"  status {order.status}  advertiser {order.advertiserId} {adv[0].name if adv else '?'}  "
          f"agency {order.agencyId}  po {order.poNumber!r}  trafficker {order.traffickerId}  "
          f"salesperson {order.salespersonId}")
    print(f"  flight {_dt(order.startDateTime)} → {_dt(order.endDateTime)}  notes {order.notes!r}")
    lis = [x for x in _q(li_svc, "getLineItemsByStatement", "orderId = :o", o=order.id)
           if not x.isArchived]
    print(f"\n{len(lis)} line items (created-on {a.created} marked *)")
    for li in lis:
        c = _dt(getattr(li, "creationDateTime", None))
        print(f"{'*' if c.startswith(a.created) else ' '} {li.id} [{li.status}] created {c}  {li.name}")
    sys.stdout.flush()
    for li in lis:
        if a.all or _dt(getattr(li, "creationDateTime", None)).startswith(a.created):
            print("\n")
            inspect(li.id, client)
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
