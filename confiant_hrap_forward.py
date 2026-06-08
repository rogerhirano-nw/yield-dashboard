"""Forward the Confiant High Risk Ad Platforms (HRAP) notice to every
SSP partner in settings.json -> confiant_outreach.ssp_contacts.

Confiant's standing recommendation for HRAPs is to forward the notice
to your demand partners so they can block these platforms upstream
before the bid ever reaches your inventory. This script produces one
Outlook draft per SSP partner with the full HRAP list, ready for you
to review and send.

Reuses the Microsoft Graph auth + draft-creation flow from
`confiant_outreach_drafts.py` so it shares the same token cache and
RFC 5322 display-name parsing.

Usage:
    # Preview without creating drafts
    python confiant_hrap_forward.py --dry-run

    # Create drafts for every SSP with a contact in settings.json
    python confiant_hrap_forward.py

    # Limit to one SSP for testing
    python confiant_hrap_forward.py --provider 'Index Exchange'
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import date
from html import escape
from pathlib import Path

import confiant_outreach
import confiant_outreach_drafts


_HRAP_FILE = Path(__file__).parent / "data" / "confiant_hraps.json"

# Newsweek brand palette — same as the weekly RevOps digest for visual
# consistency across all Confiant-sourced outbound mail.
_NW_RED       = "#d72638"
_NW_DARK      = "#1a1a1a"
_NW_TEXT      = "#222222"
_NW_MUTED     = "#6b7280"
_NW_BG_SOFT   = "#f0f4f8"
_NW_BG_LIGHT  = "#f9fafb"
_NW_BORDER    = "#e1e5eb"


def _load_hraps(path: Path) -> dict:
    return json.loads(path.read_text())


def _group_by_platform(platforms: list[dict]) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = defaultdict(list)
    for entry in platforms:
        grouped[entry.get("name", "Unknown")].append(entry["domain"])
    # Sort platforms alphabetically (case-insensitive)
    return dict(sorted(grouped.items(), key=lambda kv: kv[0].lower()))


def _platform_rows(grouped: dict[str, list[str]]) -> str:
    """Two-column table: platform name | wildcard domains (one per line)."""
    rows = []
    for name, domains in grouped.items():
        domain_html = "<br>".join(
            f"<code style='font-size:12px;color:{_NW_DARK}'>{escape(d)}</code>"
            for d in domains
        )
        rows.append(
            f"<tr>"
            f"<td style='padding:8px 14px;border-bottom:1px solid {_NW_BORDER};"
            f"font-size:13px;color:{_NW_DARK};font-weight:600;vertical-align:top;"
            f"white-space:nowrap'>{escape(name)}</td>"
            f"<td style='padding:8px 14px;border-bottom:1px solid {_NW_BORDER};"
            f"line-height:1.7'>{domain_html}</td>"
            f"</tr>"
        )
    return "".join(rows)


def render_html(ssp_name: str, publisher_name: str, payload: dict) -> tuple[str, str]:
    """Returns (subject, html_body) for one SSP HRAP forward."""
    platforms = payload.get("platforms", [])
    additions = payload.get("additions_this_update", [])
    updated_at = payload.get("updated_at", date.today().isoformat())
    grouped = _group_by_platform(platforms)
    rows_html = _platform_rows(grouped)
    additions_html = ""
    if additions:
        items = "".join(
            f"<li><strong>{escape(a.get('name','Unknown'))}</strong> &mdash; "
            f"<code>{escape(a['domain'])}</code></li>"
            for a in additions
        )
        additions_html = (
            f"<p style='margin:18px 0 6px 0;font-size:13px;color:{_NW_DARK}'>"
            f"<strong>New this update ({escape(updated_at)}):</strong></p>"
            f"<ul style='margin:0 0 18px 0;padding-left:22px;line-height:1.7'>{items}</ul>"
        )

    subject = (
        f"{ssp_name}//{publisher_name} — Confiant HRAP notice: "
        f"{len(platforms)} high-risk ad platforms to block"
    )

    html = f"""\
<html><body style='margin:0;padding:0;background:#f5f6f8;color:{_NW_TEXT};font-family:Arial,Helvetica,sans-serif;font-size:14px;line-height:1.55'>
<table role='presentation' cellpadding='0' cellspacing='0' border='0' style='background:#f5f6f8;width:100%;padding:20px 0'>
<tr><td align='center'>
<table role='presentation' cellpadding='0' cellspacing='0' border='0' style='background:#ffffff;width:100%;max-width:760px;border:1px solid {_NW_BORDER};border-radius:8px;overflow:hidden'>
  <tr><td style='background:{_NW_RED};height:4px;font-size:0;line-height:0'>&nbsp;</td></tr>
  <tr><td style='padding:20px 24px 0 24px'>
    <div style='font-size:11px;font-weight:700;color:{_NW_RED};text-transform:uppercase;letter-spacing:1.2px'>
      {escape(publisher_name)} &middot; Brand Safety
    </div>
    <h1 style='margin:6px 0 4px 0;color:{_NW_DARK};font-size:22px;font-weight:700;line-height:1.2'>
      Confiant High-Risk Ad Platform notice
    </h1>
    <div style='color:{_NW_MUTED};font-size:13px'>
      Forwarded to the {escape(ssp_name)} trust &amp; safety team
      &middot; {escape(updated_at)}
    </div>
  </td></tr>

  <tr><td style='padding:18px 24px 0 24px'>
    <p style='margin:0 0 12px 0'>Hi {escape(ssp_name)} team,</p>

    <p>{escape(publisher_name)} received a notice from Confiant flagging
    <strong>{len(platforms)} ad platforms</strong> as
    <strong>High Risk</strong> (HRAP) &mdash; bidding or serving
    intermediaries with persistent abnormal volumes of malicious campaigns.
    Confiant's recommendation is to block these at every layer, including
    upstream at our SSP partners.</p>

    <p>We're blocking them on the publisher side via our GAM
    Protection. We're forwarding this list so your trust &amp;
    safety / supply quality team can decide whether to apply equivalent
    blocks on your demand side &mdash; preventing these impressions from
    reaching us (or any other publisher in your inventory) in the first
    place.</p>

    {additions_html}

    <h2 style='margin:22px 0 8px 0;font-size:15px;color:{_NW_DARK};font-weight:700'>
      Full HRAP list ({len(platforms)} domains across {len(grouped)} platforms)
    </h2>
    <table role='presentation' cellpadding='0' cellspacing='0' border='0'
      style='border-collapse:collapse;width:100%;margin:0 0 18px 0;border:1px solid {_NW_BORDER};border-radius:6px;overflow:hidden'>
      <thead><tr>
        <th style='background:{_NW_BG_SOFT};padding:10px 14px;text-align:left;font-size:12px;color:{_NW_DARK};border-bottom:1px solid {_NW_BORDER};text-transform:uppercase;letter-spacing:0.5px'>Platform</th>
        <th style='background:{_NW_BG_SOFT};padding:10px 14px;text-align:left;font-size:12px;color:{_NW_DARK};border-bottom:1px solid {_NW_BORDER};text-transform:uppercase;letter-spacing:0.5px'>Domain(s)</th>
      </tr></thead>
      <tbody>{rows_html}</tbody>
    </table>

    <p style='color:{_NW_MUTED};font-size:12px;margin:8px 0 18px 0'>
      Source: Confiant Support periodic HRAP notice. Reach out to
      <a href='mailto:support@confiant.com' style='color:#1a73e8'>support@confiant.com</a>
      for questions about the methodology or to dispute a listing.
    </p>
  </td></tr>

  <tr><td style='padding:14px 24px 18px 24px;background:{_NW_BG_LIGHT};border-top:1px solid {_NW_BORDER}'>
    <p style='margin:0;font-size:12px;color:{_NW_MUTED};line-height:1.6'>
      Thanks,<br>
      Roger Hirano<br>
      Yield &amp; Ad Quality, {escape(publisher_name)}
    </p>
  </td></tr>
</table>
</td></tr></table>
</body></html>"""
    return subject, html


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Create one Outlook draft per SSP forwarding the "
                    "Confiant HRAP list.",
    )
    p.add_argument("--hrap-file", type=Path, default=_HRAP_FILE,
                   help=f"HRAP list JSON (default {_HRAP_FILE}).")
    p.add_argument("--provider", default=None,
                   help="Only create the draft for this SSP (for testing).")
    p.add_argument("--dry-run", action="store_true",
                   help="Render emails + print summary, don't create drafts.")
    p.add_argument("--auth-only", action="store_true",
                   help="Refresh/acquire the Graph token and exit.")
    return p.parse_args()


def main() -> int:
    confiant_outreach._load_dotenv()
    args = _parse_args()

    if not args.hrap_file.exists():
        print(f"ERROR: HRAP file not found: {args.hrap_file}", file=sys.stderr)
        return 2

    payload = _load_hraps(args.hrap_file)
    print(f"Loaded HRAP list: {len(payload.get('platforms', []))} domains, "
          f"updated {payload.get('updated_at')}")

    # ─── 1. Authenticate (skip in --dry-run) ────────────────────────────────
    token = None
    if not args.dry_run:
        print("\nStep 1: Authenticate with Microsoft Graph")
        token = confiant_outreach_drafts.get_token(interactive_ok=not args.auth_only)
        if args.auth_only:
            print("Token refreshed; exiting.")
            return 0
        print("  ✓ Got Graph token")

    # ─── 2. Load SSP distro ────────────────────────────────────────────────
    repo_root = Path(__file__).parent
    cfg = confiant_outreach._load_settings(repo_root)
    contacts = {
        ssp: addrs for ssp, addrs in cfg.ssp_contacts.items()
        if not addrs.lower().startswith("replace_me")
    }
    if args.provider:
        if args.provider not in contacts:
            print(f"ERROR: --provider {args.provider!r} not found "
                  f"or has no contact set in settings.json", file=sys.stderr)
            return 2
        contacts = {args.provider: contacts[args.provider]}

    print(f"\nStep 2: Generate drafts for {len(contacts)} SSP partner"
          f"{'s' if len(contacts) != 1 else ''}")

    cc_emails = cfg.cc_emails or []

    # ─── 3. Create one draft per SSP ────────────────────────────────────────
    created = 0
    for ssp_name in sorted(contacts.keys()):
        to_emails = confiant_outreach._split_recipients(contacts[ssp_name])
        subject, html = render_html(ssp_name, cfg.publisher_name, payload)
        if args.dry_run:
            print(f"  [dry-run] {ssp_name:<22} -> {', '.join(to_emails):<60} "
                  f"({len(html):,} bytes)")
            continue
        try:
            confiant_outreach_drafts.create_graph_draft(
                token=token,
                subject=subject,
                html_body=html,
                to_emails=to_emails,
                cc_emails=cc_emails,
            )
            print(f"  [draft] {ssp_name:<22} -> {', '.join(to_emails)}")
            created += 1
        except Exception as e:
            print(f"  [FAIL] {ssp_name:<22} -> {', '.join(to_emails)} :: {e}",
                  file=sys.stderr)

    if args.dry_run:
        print(f"\nDry-run complete. Would create {len(contacts)} drafts.")
    else:
        print(f"\nCreated {created} drafts. Open Outlook -> Drafts to review and send.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
