"""Read-only GAM Ad Review Center lookup (REST v1 `adReviewCenterAds:search`).

Two questions, no writes:
  1. For each GPT Ad Response ID, which ARC ad (creative) did it serve, and
     what is that ad's status now?  (`adResponseId` filter — the API form of
     the UI's "Ad response ID:" filter that gam_arc.block_in_arc drives.)
  2. What is the status of a given ARC ad ID?  (`adReviewCenterAdId` filter.)

Searched across the network's web properties (display / videoAndAudio /
mobileApp), since an ARC ad lives under exactly one. Preview URLs are dropped
from the output (they're signed and the Actions logs are public).

Usage:
  GAM_SERVICE_ACCOUNT_JSON=… GAM_NETWORK_ID=… python scripts/arc_lookup.py \
      --ad-id 'AAyH9e…==' --response-id CKv75e… --response-id …
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import requests
from google.auth.transport.requests import Request
from google.oauth2 import service_account

BASE = "https://admanager.googleapis.com/v1"
WEB_PROPERTIES = ("display", "videoAndAudio", "mobileApp")
DROP = ("previewUrl", "assetPreviewUrls")


def _token() -> str:
    creds = service_account.Credentials.from_service_account_info(
        json.loads(os.environ["GAM_SERVICE_ACCOUNT_JSON"]),
        scopes=["https://www.googleapis.com/auth/admanager"],
    )
    creds.refresh(Request())
    return creds.token


def search(token: str, network: str, prop: str, params: dict) -> tuple[list[dict], str | None]:
    url = f"{BASE}/networks/{network}/webProperties/{prop}/adReviewCenterAds:search"
    r = requests.get(url, params=params, headers={"Authorization": f"Bearer {token}"}, timeout=60)
    if r.status_code != 200:
        return [], f"HTTP {r.status_code}: {r.text[:300]}"
    ads = r.json().get("adReviewCenterAds") or []
    return [{k: v for k, v in a.items() if k not in DROP} for a in ads], None


class PermissionDenied(RuntimeError):
    pass


def lookup(token: str, network: str, label: str, params: dict) -> list[dict]:
    found: list[dict] = []
    errors = 0
    for prop in WEB_PROPERTIES:
        ads, err = search(token, network, prop, params)
        if err:
            if err.startswith("HTTP 403"):
                # A 403 is the service account's role, not the ad: say so once
                # and stop, rather than reporting every ID as "not found".
                raise PermissionDenied(err)
            errors += 1
            print(f"  [{prop}] {err}")
        for a in ads:
            a["_webProperty"] = prop
            found.append(a)
    if not found:
        print(f"  {label}: " + ("lookup failed" if errors == len(WEB_PROPERTIES)
                                else "no ARC ad found"))
    for a in found:
        print(f"  {label}: [{a['_webProperty']}] ad={a.get('adReviewCenterAdId')} "
              f"status={a.get('status')} manual={a.get('manualReviewStatuses')} "
              f"advertiser={a.get('advertiserDisplayName')!r} "
              f"destinations={a.get('destinationUrls')}")
    return found


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--ad-id", action="append", default=[])
    p.add_argument("--response-id", action="append", default=[])
    a = p.parse_args()
    network = os.environ["GAM_NETWORK_ID"]
    token = _token()
    try:
        return _run(token, network, a)
    except PermissionDenied as e:
        print(f"PERMISSION_DENIED — the service account's GAM role can't read Ad "
              f"Review Center, so nothing was looked up. {str(e)[:200]}")
        return 1


def _run(token: str, network: str, a) -> int:
    targets = set(a.ad_id)
    print("=== ARC ad ID lookup ===")
    for ad_id in a.ad_id:
        lookup(token, network, f"ad {ad_id[:16]}…", {"adReviewCenterAdId": ad_id})

    print("\n=== GPT Ad Response ID lookup ===")
    matches = []
    for rid in a.response_id:
        for ad in lookup(token, network, rid, {"adResponseId": rid}):
            if ad.get("adReviewCenterAdId") in targets:
                matches.append(rid)
    print(f"\nResponse IDs that served a target ad: {matches or 'none'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
