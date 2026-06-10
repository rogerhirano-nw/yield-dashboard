"""One-off GAM availability-forecast probe (read-only).

Asks ForecastService.getAvailabilityForecast how many impressions a
hypothetical line item could capture: "if we sold a STANDARD CPM line at
<size> against <ad unit> for the next <days> days, how much inventory
matches and how much is still unreserved?"

ForecastService is SOAP-only (not in the v1 REST API), so this rides the
same lazy `GAMClient._get_soap_client()` used for creatives/LICA. Forecast
calls create nothing in GAM.

Usage:
    python scripts/gam_avails_probe.py                       # RON 300x250, 30 days
    python scripts/gam_avails_probe.py --width 970 --height 250 --days 14
    python scripts/gam_avails_probe.py --ad-unit 12345678 --contending
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import date, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

with open(REPO_ROOT / ".env") as f:
    for line in f:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.strip()
        if v.startswith('"') and v.endswith('"'):
            v = v[1:-1]
        os.environ.setdefault(k.strip(), v)

from gam_client import GAMClient  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--width", type=int, default=300)
    ap.add_argument("--height", type=int, default=250)
    ap.add_argument("--days", type=int, default=30, help="forecast horizon from today")
    ap.add_argument("--goal", type=int, default=1_000_000,
                    help="hypothetical impression goal (affects reservedUnits only)")
    ap.add_argument("--ad-unit", default=None,
                    help="ad unit id to target (default: network root = run of network)")
    ap.add_argument("--contending", action="store_true",
                    help="also list line items competing for the same inventory")
    args = ap.parse_args()

    gc = GAMClient()
    client = gc._get_soap_client()
    version = GAMClient._SOAP_API_VERSION

    network = client.GetService("NetworkService", version=version).getCurrentNetwork()
    ad_unit_id = args.ad_unit or network["effectiveRootAdUnitId"]
    tz = network["timeZone"]
    print(f"network {network['displayName']} ({network['networkCode']}), "
          f"ad unit {ad_unit_id}, {args.width}x{args.height}, next {args.days} days")

    end = date.today() + timedelta(days=args.days)
    prospective = {
        "lineItem": {
            "lineItemType": "STANDARD",
            "startDateTimeType": "IMMEDIATELY",
            "endDateTime": {
                "date": {"year": end.year, "month": end.month, "day": end.day},
                "hour": 23, "minute": 59, "second": 59,
                "timeZoneId": tz,
            },
            "costType": "CPM",
            "creativePlaceholders": [
                {"size": {"width": args.width, "height": args.height,
                          "isAspectRatio": False}}
            ],
            "primaryGoal": {
                "goalType": "LIFETIME",
                "unitType": "IMPRESSIONS",
                "units": args.goal,
            },
            "targeting": {
                "inventoryTargeting": {
                    "targetedAdUnits": [
                        {"adUnitId": ad_unit_id, "includeDescendants": True}
                    ]
                }
            },
        }
    }

    svc = client.GetService("ForecastService", version=version)
    fc = svc.getAvailabilityForecast(
        prospective,
        {"includeContendingLineItems": args.contending,
         "includeTargetingCriteriaBreakdown": False},
    )

    for field in ("matchedUnits", "availableUnits", "possibleUnits",
                  "deliveredUnits", "reservedUnits"):
        print(f"{field:>16}: {fc[field]:>15,}")
    print(f"{'unitType':>16}: {fc['unitType']}")

    if args.contending and fc["contendingLineItems"]:
        print("\ncontending line items (top 10 by contending impressions):")
        top = sorted(fc["contendingLineItems"],
                     key=lambda c: c["contendingImpressions"], reverse=True)[:10]
        for c in top:
            print(f"  LI {c['lineItemId']}: {c['contendingImpressions']:,}")


if __name__ == "__main__":
    main()
