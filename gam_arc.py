"""Block Confiant-flagged Cloaked Google creatives in GAM Ad Review Center.

Reusable helpers used by:
  - scripts/confiant_gam_arc_block.py (manual one-off / Alert Log batch)
  - confiant_blocklist.py             (daily cron Phase 2, after URL push)

For Cloaked rows where Confiant's `issue_type_by_domain` API returns
`Detail = ID xxxxx` instead of a domain, the only blocking action available
on the publisher side is a per-creative block in GAM's Ad Review Center,
filtered by the GPT Ad Response ID Confiant exposes on each adtrace page.

Three gotchas baked into the flow (see docs/confiant_blocklist.md
"Manual blocks in GAM Ad Review Center" for the field debrief):

  1. GAM moved the Ad Review Center URL from `#creatives/ad_review_center`
     to `#brand_safety/ad_review_center` — use the new one.
  2. The `Ad response ID` filter only shows up in the autocomplete dropdown
     after you type a value-shaped string. Type first, then click the
     autocomplete menuitem with the `Ad response ID:` prefix.
  3. Low-impression cards hang in skeleton-render state forever — the
     `Block ad` button never appears. The fix is to reload the page
     after the filter is applied; GAM persists the filter as `&as=<blob>`
     in the URL hash and post-reload hydration renders the card properly.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

GPT_LABEL_RE = re.compile(r"GPT Ad Response ID\s+(\S+)", re.IGNORECASE)
CLOAKED_ID_RE = re.compile(r"^ID\s+\d+$", re.IGNORECASE)


def arc_url(network_id: str) -> str:
    return (f"https://admanager.google.com/{network_id}"
            "#brand_safety/ad_review_center")


def extract_gpt_id(page) -> str | None:
    """Regex `GPT Ad Response ID\\s+(\\S+)` out of a Confiant adtrace page body.
    Returns None if not found (page wasn't a Confiant adtrace, or the layout
    changed)."""
    body = page.inner_text("body")
    m = GPT_LABEL_RE.search(body)
    return m.group(1).strip() if m else None


def apply_ad_response_id_filter(page, gpt_id: str) -> None:
    """Type the GPT ID into the ARC filter input, then click the
    `Ad response ID:` autocomplete option. NOT `Text search:` — that
    would search across many fields and not find anything useful.
    Raises RuntimeError if the autocomplete menuitem can't be found
    (GAM changed the filter UI)."""
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
            "changed the filter UI. Re-probe with --inspect."
        )
    opt.click()
    page.wait_for_timeout(8000)


def block_in_arc(page, gpt_id: str, network_id: str) -> str:
    """Filter the ARC by `Ad response ID: <gpt_id>` and click Block on the
    matching card. Returns one of:

      "blocked"      — Block clicked successfully, state.sqlite caller can record
      "not-in-arc"   — Filter applied, ARC showed "Couldn't find matching ad"
                       (commonly: served by an Open Bidding partner, not GAM)
      "no-block-btn" — Card present but Block button didn't surface even after
                       the reload trick. Rare; usually fixed by a manual visit.
    """
    page.goto(arc_url(network_id), wait_until="load", timeout=60000)
    page.wait_for_timeout(7000)
    apply_ad_response_id_filter(page, gpt_id)

    if "Couldn't find matching ad" in page.inner_text("body"):
        return "not-in-arc"

    # Wait for the Block button. If it doesn't appear, try the reload trick:
    # GAM persists the filter as &as=<blob> in the URL hash, post-reload
    # hydration renders skeleton-stuck cards properly.
    try:
        page.locator('button[aria-label="Block ad"]').first.wait_for(
            state="visible", timeout=15000
        )
    except Exception:
        page.reload(wait_until="load")
        page.wait_for_timeout(15000)
        try:
            page.locator('button[aria-label="Block ad"]').first.wait_for(
                state="visible", timeout=20000
            )
        except Exception:
            return "no-block-btn"

    card = page.locator('div:has-text("Ad match")').first
    try:
        card.hover()
        page.wait_for_timeout(800)
    except Exception:
        pass

    page.locator('button[aria-label="Block ad"]').first.click()
    page.wait_for_timeout(4000)

    # Confirm dialog (if any)
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


def record_arc_block(
    state_path: Path,
    gpt_id: str,
    confiant_id: str | None,
    protection_id: int = 28044902,
) -> None:
    """Insert a row into `blocked_domains` so the weekly digest shows it
    under its own ARC-blocks bucket (distinct from URL-based Protection
    pushes)."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    cid_part = f"Confiant ID {confiant_id} → " if confiant_id else ""
    it = f"Manual block in GAM ARC — {cid_part}GPT {gpt_id}"
    with sqlite3.connect(state_path) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO blocked_domains "
            "(domain, issue_type, first_seen_in_csv, first_pushed_to_gam, "
            " protection_id, protection_label) VALUES (?,?,?,?,?,?)",
            (f"gam-arc:{gpt_id}", it, now[:10], now,
             protection_id, "ARC manual block"),
        )
        conn.commit()


def already_arc_blocked(state_path: Path) -> set[str]:
    """Return the set of GPT Ad Response IDs we've already recorded as
    ARC-blocked. The daily cron's ARC phase uses this to skip repeats."""
    try:
        with sqlite3.connect(state_path) as conn:
            cur = conn.execute(
                "SELECT domain FROM blocked_domains WHERE domain LIKE 'gam-arc:%'"
            )
            return {row[0][len("gam-arc:"):] for row in cur}
    except Exception:
        return set()
