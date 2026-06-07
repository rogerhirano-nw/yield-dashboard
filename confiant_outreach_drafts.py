"""
confiant_outreach_drafts — generate Confiant SSP outreach emails as DRAFTS
in your Outlook (New Outlook on Mac compatible) via Microsoft Graph API.

Why this exists:
  * AppleScript drafts don't show up in New Outlook's Drafts folder.
  * Microsoft Graph is the official, supported way; drafts created via
    Graph appear in any Outlook UI (New, Classic, web, mobile, all sync).

First-run setup:
  Run the script. It prints a device code + URL. Open the URL in any
  browser, enter the code, sign in with your Newsweek work account.
  Token caches at ~/.confiant-outreach/msal_cache.json — subsequent runs
  auto-authenticate without prompting.

Order matters: auth happens FIRST (within ~30s) before the slow Confiant
API poll (~60s). This way the 15-minute device code window doesn't get
eaten by the Confiant report poll.

Usage:
  python confiant_outreach_drafts.py
  python confiant_outreach_drafts.py --provider Xandr   # test one SSP
  python confiant_outreach_drafts.py --dry-run          # preview, no drafts
  python confiant_outreach_drafts.py --auth-only        # refresh token, exit
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import confiant_client
import confiant_outreach


# Microsoft's "Microsoft Graph PowerShell" public client app — consent
# pre-approved in most tenants, supports device-code flow without
# Azure AD app registration.
_CLIENT_ID = "14d82eec-204b-4c2f-b7e8-296a70dab67e"
_AUTHORITY = "https://login.microsoftonline.com/common"
_SCOPES = ["Mail.ReadWrite"]
_CACHE_PATH = Path("~/.confiant-outreach/msal_cache.json").expanduser()
_GRAPH = "https://graph.microsoft.com/v1.0"


def get_token(interactive_ok: bool = True) -> str:
    """Acquire a Graph access token via device-code flow.

    Uses raw OAuth2 endpoints + a simple JSON cache file. Avoids MSAL's
    `acquire_token_by_device_flow` which has a regression in 1.37 that
    exits after the first 'authorization_pending' response instead of
    polling for the full 15-min device-code lifetime.

    Cache file format ({access_token, refresh_token, expires_at}) is our
    own — kept simple so the silent-refresh path works reliably across
    runs without depending on MSAL cache semantics.
    """
    import time
    import requests

    _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    token_endpoint = f"{_AUTHORITY}/oauth2/v2.0/token"

    # 1. Cached access token (still valid?)
    if _CACHE_PATH.exists():
        try:
            cached = json.loads(_CACHE_PATH.read_text())
            if cached.get("expires_at", 0) > time.time() + 60:
                print(f"  ✓ Using cached access token "
                      f"(expires in {int(cached['expires_at'] - time.time())}s)",
                      flush=True)
                return cached["access_token"]
            # 2. Try refresh-token refresh
            rt = cached.get("refresh_token")
            if rt:
                r = requests.post(
                    token_endpoint,
                    data={
                        "grant_type": "refresh_token",
                        "client_id": _CLIENT_ID,
                        "refresh_token": rt,
                        "scope": " ".join(_SCOPES) + " offline_access",
                    },
                    timeout=30,
                )
                d = r.json()
                if "access_token" in d:
                    print(f"  ✓ Refreshed token silently", flush=True)
                    _save_token_cache(d)
                    return d["access_token"]
                print(f"  refresh failed ({d.get('error', '?')}), "
                      f"falling back to interactive sign-in", flush=True)
        except Exception as e:
            print(f"  cache read failed: {e}", flush=True)

    if not interactive_ok:
        raise RuntimeError("No cached token and interactive=False")

    # 3. Device-code flow with manual polling
    flow_r = requests.post(
        f"{_AUTHORITY}/oauth2/v2.0/devicecode",
        data={"client_id": _CLIENT_ID, "scope": " ".join(_SCOPES) + " offline_access"},
        timeout=30,
    )
    flow = flow_r.json()
    if "device_code" not in flow:
        raise RuntimeError(f"Couldn't start device flow: {flow}")

    print()
    print("=" * 64, flush=True)
    print(" Microsoft sign-in required", flush=True)
    print("=" * 64, flush=True)
    print(flow["message"], flush=True)
    print("=" * 64, flush=True)
    print("\nPolling for sign-in... (you have ~15 min from now)", flush=True)

    interval = int(flow.get("interval", 5))
    deadline = time.time() + int(flow.get("expires_in", 900))

    while time.time() < deadline:
        time.sleep(interval)
        r = requests.post(
            token_endpoint,
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                "client_id": _CLIENT_ID,
                "device_code": flow["device_code"],
            },
            timeout=30,
        )
        d = r.json()
        if "access_token" in d:
            print("  ✓ Sign-in complete", flush=True)
            _save_token_cache(d)
            return d["access_token"]
        err = d.get("error", "")
        if err == "authorization_pending":
            continue
        if err == "slow_down":
            interval += 5
            continue
        raise RuntimeError(f"Auth failed: {d.get('error_description', d)}")

    raise TimeoutError("Device code expired — sign-in not completed in 15 min")


def _save_token_cache(token_response: dict) -> None:
    """Persist access + refresh token to disk in our cache format."""
    import time
    _CACHE_PATH.write_text(json.dumps({
        "access_token": token_response["access_token"],
        "refresh_token": token_response.get("refresh_token"),
        "expires_at": time.time() + int(token_response.get("expires_in", 3600)),
    }))


def create_graph_draft(
    token: str,
    subject: str,
    html_body: str,
    to_emails: list[str],
    cc_emails: list[str] | None = None,
) -> str:
    """Create a draft via Microsoft Graph. Returns the new message ID."""
    import requests

    payload = {
        "subject": subject,
        "body": {"contentType": "HTML", "content": html_body},
        "toRecipients": [
            {"emailAddress": {"address": e.strip()}} for e in to_emails
        ],
    }
    if cc_emails:
        payload["ccRecipients"] = [
            {"emailAddress": {"address": e.strip()}} for e in cc_emails
        ]

    r = requests.post(
        f"{_GRAPH}/me/messages",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json=payload, timeout=30,
    )
    if r.status_code not in (200, 201):
        raise RuntimeError(f"Graph API error {r.status_code}: {r.text[:500]}")
    return r.json()["id"]


# ── orchestrator ─────────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Create per-SSP Confiant outreach drafts in your Outlook.",
    )
    p.add_argument("--days", type=int, default=7,
                   help="Lookback window (default 7).")
    p.add_argument("--provider", default=None,
                   help="Create draft for only this Provider.")
    p.add_argument("--dry-run", action="store_true",
                   help="Render emails + print summary, don't create drafts.")
    p.add_argument("--auth-only", action="store_true",
                   help="Refresh / acquire the Graph token and exit.")
    p.add_argument("--csv", default=None,
                   help="Use a local CSV instead of the Confiant API.")
    return p.parse_args()


def main() -> int:
    confiant_outreach._load_dotenv()
    args = _parse_args()

    # ────────────────────────────────────────────────────────────
    # Step 1: AUTHENTICATE FIRST (before the slow Confiant API pull).
    # Device code is valid for 15 min. The earlier version did
    # Confiant pull (60s) → auth (prompt user) → tokens expire while
    # user is hunting for browser. Doing auth first means user has
    # the full 15-min window to sign in.
    # ────────────────────────────────────────────────────────────
    if not args.dry_run:
        print("Step 1: Authenticate with Microsoft Graph")
        token = get_token(interactive_ok=True)
        print(f"  ✓ Got Graph token")
        if args.auth_only:
            print("\n--auth-only: token cached, exiting.")
            return 0
    else:
        token = None

    # ────────────────────────────────────────────────────────────
    # Step 2: Settings + Confiant data
    # ────────────────────────────────────────────────────────────
    print()
    print("Step 2: Load settings + pull Confiant data")
    cfg = confiant_outreach._load_settings(Path(__file__).parent)
    if not cfg.ssp_contacts:
        print("settings.json has no confiant_outreach.ssp_contacts map.",
              file=sys.stderr)
        return 2

    if args.csv:
        report = confiant_client.parse_csv(args.csv)
        print(f"  ✓ Loaded CSV from {args.csv}")
    else:
        report = confiant_client.fetch_via_api(days=args.days)
        print(f"  ✓ Confiant API: {len(report.rows):,} rows")

    by_provider = confiant_outreach.group_by_provider(
        report, cfg.min_flagged_impressions,
    )
    if args.provider:
        if args.provider not in by_provider:
            print(f"No flags for {args.provider!r} "
                  f"(known: {sorted(by_provider)})", file=sys.stderr)
            return 1
        by_provider = {args.provider: by_provider[args.provider]}

    # ────────────────────────────────────────────────────────────
    # Step 3: Create drafts (one per SSP with a contact)
    # ────────────────────────────────────────────────────────────
    print()
    print(f"Step 3: Create drafts ({len(by_provider)} providers w/ flagged content)")
    created = 0
    skipped = 0

    for provider, pf in sorted(
        by_provider.items(), key=lambda kv: -len(kv[1].rows),
    ):
        recipient_raw = cfg.ssp_contacts.get(provider)
        if not recipient_raw or recipient_raw.startswith("REPLACE_ME"):
            skipped += 1
            print(f"  [skip] {provider}: {len(pf.rows)} creatives "
                  f"— no contact in settings.json")
            continue

        subject, html = confiant_outreach.render_email_html(
            pf, cfg.publisher_name, args.days,
        )
        to_emails = confiant_outreach._split_recipients(recipient_raw)

        if args.dry_run:
            print(f"  [dry-run] {provider} -> {', '.join(to_emails)} "
                  f"({len(pf.rows)} creatives)")
            continue

        try:
            create_graph_draft(
                token=token, subject=subject, html_body=html,
                to_emails=to_emails, cc_emails=cfg.cc_emails or None,
            )
            created += 1
            print(f"  [draft] {provider:<20} -> {', '.join(to_emails):<50} "
                  f"({len(pf.rows)} creatives)")
        except Exception as e:
            print(f"  [error] {provider} -> {recipient_raw}: {e}",
                  file=sys.stderr)

    print()
    if args.dry_run:
        print(f"Dry-run complete. Would create {len(by_provider) - skipped} "
              f"drafts, skip {skipped}.")
    else:
        print(f"Created {created} drafts.  Skipped {skipped} (no contact).")
        print("Open Outlook → Drafts to review and send.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
