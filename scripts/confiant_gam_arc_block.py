"""Block Confiant-flagged Cloaked Google creatives in GAM Ad Review Center.

Companion to `confiant_blocklist.py`. That script handles the destination-URL
case via GAM Protection. This script handles the **cloaked / no-destination**
case: when Confiant's `issue_type_by_domain` API returns `Detail = ID xxxxx`
instead of a domain, we ask Confiant for the GPT Ad Response IDs (either via
Alert Log export or a one-off ask to support) and use them to filter + block
the matching creatives in GAM's Ad Review Center.

Two-phase flow per Confiant adtrace URL:

  1. Open the Confiant adtrace page in the persistent profile, extract the
     `GPT Ad Response ID` from the page body.
  2. Navigate to GAM's Ad Review Center, apply the `Ad response ID` filter
     (NOT a generic Text search — the autocomplete shows the right option
     once you start typing the value), click the Block button on the
     matching card.

Things this script has learned the hard way:

- GAM moved the Ad Review Center from `#creatives/ad_review_center` to
  `#brand_safety/ad_review_center`. We use the new URL.
- The `Ad response ID` filter only shows up in the autocomplete after you
  start typing the value. Type first, then click the menuitem.
- For low-impression creatives (≤ ~5 imps), the ARC card can hang in
  skeleton-render state and the Block button never appears. The fix that
  works: apply the filter, then **reload the page** (GAM persists the
  filter in the URL hash as `&as=…`), and the post-reload hydration
  renders the card properly. The script does this automatically when the
  Block button isn't visible after 15s.
- Every successful block lands in `state.sqlite` as
  `gam-arc:<gpt_ad_response_id>` with
  `issue_type = "Manual block in GAM ARC — Confiant ID xxxxx → GPT ..."`
  so it surfaces in the weekly RevOps digest under its own bucket.

Usage:

  # Process a list of adtrace URLs from JSON (default)
  python scripts/confiant_gam_arc_block.py /tmp/confiant_queue.json

  # …or one or more adtrace URLs inline
  python scripts/confiant_gam_arc_block.py \
      https://app.confiant.com/adtrace/abc... \
      https://app.confiant.com/adtrace/def...

  # Dry-run: extract GPT IDs only, don't drive GAM
  python scripts/confiant_gam_arc_block.py --dry-run /tmp/queue.json

The JSON input format is the same shape `confiant_blocklist.py` emits for
its cloaked-for-review section, plus we accept a simpler bare-list shape:

  [{"adtrace_url": "https://app.confiant.com/adtrace/abc...",
    "confiant_id": "17769",   # optional, for traceability in state.sqlite
    "imps": 67}, ...]
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Reuse env / state helpers from confiant_blocklist.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from confiant_blocklist import _load_dotenv, _state_path  # noqa: E402


PROFILE = Path("~/.confiant-blocklist/playwright-profile").expanduser()
GAM_NETWORK = "22541732127"  # Newsweek
ARC_URL = (
    f"https://admanager.google.com/{GAM_NETWORK}"
    "#brand_safety/ad_review_center"
)
GPT_LABEL_RE = re.compile(r"GPT Ad Response ID\s+(\S+)", re.IGNORECASE)


def _extract_gpt_id(page) -> str | None:
    body = page.inner_text("body")
    m = GPT_LABEL_RE.search(body)
    return m.group(1).strip() if m else None


def _apply_ad_response_id_filter(page, gpt_id: str) -> None:
    """Type the GPT ID into the filter input + click the 'Ad response ID:'
    autocomplete option (not 'Text search:')."""
    fi = page.locator('input[placeholder*="Filter"]').first
    fi.click()
    page.wait_for_timeout(500)
    fi.fill("")
    page.keyboard.type(gpt_id, delay=20)
    page.wait_for_timeout(2000)
    opt = page.locator('[role="menuitem"]:has-text("Ad response ID:")').first
    if opt.count() == 0:
        raise RuntimeError(
            "'Ad response ID:' autocomplete option missing — GAM may have "
            "changed the filter UI. Re-probe with /tmp/bs_autocomplete_*.png."
        )
    opt.click()
    page.wait_for_timeout(8000)


def _block_in_arc(page, gpt_id: str, confiant_id: str | None = None) -> str:
    """Filter + block one GPT ID. Returns 'blocked' / 'not-in-arc' / status."""
    page.goto(ARC_URL, wait_until="load", timeout=60000)
    page.wait_for_timeout(7000)
    _apply_ad_response_id_filter(page, gpt_id)

    # Did the filter return nothing?
    if "Couldn't find matching ad" in page.inner_text("body"):
        return "not-in-arc"  # Confiant RTB already caught it upstream

    # Wait for the Block button. If it doesn't appear, try the reload trick.
    try:
        page.locator('button[aria-label="Block ad"]').first.wait_for(
            state="visible", timeout=15000
        )
    except Exception:
        # The reload trick: GAM persists the filter as &as=… in the URL hash.
        # Post-reload hydration renders skeleton-stuck cards properly.
        print("    Block button absent after 15s — applying reload trick...")
        page.reload(wait_until="load")
        page.wait_for_timeout(15000)
        try:
            page.locator('button[aria-label="Block ad"]').first.wait_for(
                state="visible", timeout=20000
            )
        except Exception:
            return "no-block-btn"  # even the reload trick failed

    # Hover the matching card (some action buttons appear on hover).
    card = page.locator('div:has-text("Ad match")').first
    try:
        card.hover()
        page.wait_for_timeout(800)
    except Exception:
        pass

    page.locator('button[aria-label="Block ad"]').first.click()
    page.wait_for_timeout(4000)

    # If a confirmation dialog appears, click the primary Block button.
    for sel in (
        '[role="dialog"] button:has-text("Block")',
        '[role="dialog"] button:has-text("Confirm")',
    ):
        c = page.locator(sel).last
        if c.count() and c.is_visible():
            try:
                c.click()
                page.wait_for_timeout(2000)
                break
            except Exception:
                pass

    return "blocked"


def _record(gpt_id: str, confiant_id: str | None) -> None:
    """One row per block in state.sqlite for traceability in the weekly digest."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    cid_label = f"Confiant ID {confiant_id} → " if confiant_id else ""
    it = f"Manual block in GAM ARC — {cid_label}GPT {gpt_id}"
    with sqlite3.connect(_state_path()) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO blocked_domains "
            "(domain, issue_type, first_seen_in_csv, first_pushed_to_gam, "
            " protection_id, protection_label) VALUES (?,?,?,?,?,?)",
            (f"gam-arc:{gpt_id}", it, now[:10], now, 28044902,
             "ARC manual block"),
        )
        conn.commit()


def _load_input(argv_paths_or_urls: list[str]) -> list[dict]:
    """Accept either a JSON file path (preferred) or raw adtrace URLs."""
    out: list[dict] = []
    for arg in argv_paths_or_urls:
        if arg.startswith("http"):
            out.append({"adtrace_url": arg})
        else:
            data = json.loads(Path(arg).read_text())
            if isinstance(data, list):
                out.extend(data)
            elif isinstance(data, dict) and "mappings" in data:
                out.extend({"adtrace_url": m.get("adtrace_url"),
                            "confiant_id": str(m.get("id")),
                            "imps": m.get("imps")} for m in data["mappings"]
                           if m.get("adtrace_url"))
            else:
                raise ValueError(f"Unrecognized JSON shape in {arg}")
    return [r for r in out if r.get("adtrace_url")]


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("inputs", nargs="+",
                   help="Either Confiant adtrace URLs or paths to a JSON file "
                        "containing them.")
    p.add_argument("--dry-run", action="store_true",
                   help="Phase 1 only — pull GPT IDs from Confiant, don't drive GAM.")
    args = p.parse_args()
    _load_dotenv()

    items = _load_input(args.inputs)
    if not items:
        print("No adtrace URLs to process.", file=sys.stderr)
        return 2
    print(f"Processing {len(items)} adtrace URL(s)\n")

    from playwright.sync_api import sync_playwright
    results: list[dict] = []
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            str(PROFILE), headless=False, viewport={"width": 1500, "height": 950},
        )
        confiant_page = ctx.pages[0] if ctx.pages else ctx.new_page()

        # Phase 1: pull GPT IDs from Confiant
        print("=== Phase 1: GPT Ad Response IDs ===\n")
        for item in items:
            confiant_page.goto(item["adtrace_url"], wait_until="load",
                               timeout=60000)
            confiant_page.wait_for_timeout(5000)
            gpt = _extract_gpt_id(confiant_page)
            item["gpt_ad_response_id"] = gpt
            cid = item.get("confiant_id", "?")
            print(f"  Confiant {cid}: {gpt or '(NOT FOUND)'}")

        if args.dry_run:
            print("\n--dry-run: skipping GAM ARC phase.")
            print(json.dumps(items, indent=2))
            ctx.close()
            return 0

        # Phase 2: block each in GAM ARC (fresh tab per ID — avoids stale chips)
        print("\n=== Phase 2: GAM Ad Review Center blocks ===\n")
        for item in items:
            gpt = item.get("gpt_ad_response_id")
            if not gpt:
                item["arc_status"] = "skip-no-gpt-id"
                continue
            cid = item.get("confiant_id")
            print(f"  Confiant {cid} → {gpt}")
            arc_page = ctx.new_page()
            try:
                status = _block_in_arc(arc_page, gpt, cid)
                item["arc_status"] = status
                if status == "blocked":
                    _record(gpt, cid)
                    print(f"    → BLOCKED + recorded")
                else:
                    print(f"    → {status}")
            except Exception as e:
                item["arc_status"] = f"error: {e}"
                print(f"    → ERROR: {e}", file=sys.stderr)
            finally:
                arc_page.close()
            results.append(item)

        ctx.close()

    blocked = sum(1 for r in items if r.get("arc_status") == "blocked")
    not_in_arc = sum(1 for r in items if r.get("arc_status") == "not-in-arc")
    print(f"\nSummary: {blocked} blocked, {not_in_arc} not in ARC "
          f"(already handled by Confiant RTB), "
          f"{len(items) - blocked - not_in_arc} other.")
    return 0 if blocked or not_in_arc == len(items) else 1


if __name__ == "__main__":
    sys.exit(main())
