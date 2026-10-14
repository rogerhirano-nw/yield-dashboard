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


# Statuses block_in_arc returns when it refuses to click. Every one of these
# means "nothing was blocked" — the caller must not record a block.
REFUSED_STATUSES = ("ambiguous", "deal-ad", "unconfirmed", "no-block-btn")

# Deal channels whose ads must never be auto-blocked: blocking a creative in
# ARC blocks it everywhere, including the PG/PD deals a buyer has paid for.
_DEAL_MARKERS = ("programmatic guaranteed", "preferred deal")

_NOT_FOUND = "Couldn't find matching ad"
_BLOCK_BTN = 'button[aria-label="Block ad"]'


def _visible_block_buttons(page) -> int:
    btns = page.locator(_BLOCK_BTN)
    return sum(1 for i in range(btns.count()) if btns.nth(i).is_visible())


def _wait_for_filtered_result(page, timeout_ms: int) -> str:
    """Poll until ARC shows a settled, filtered result.

    Returns "not-in-arc" (the no-match message), "one" (exactly one ad card
    with a Block button), "many" (more than one — the filter didn't narrow the
    list), or "none" (nothing rendered before the timeout).

    The 2026-09 Apple PG incident: the old flow waited a fixed 8s and then
    clicked the FIRST Block button on the page. When GAM was slower than 8s
    the page was still the unfiltered ARC feed, so the cron blocked whatever
    high-volume ad sat at the top — Apple PG creatives Confiant never flagged.
    "many" is exactly that state, and it is never a reason to click.
    """
    waited = 0
    step = 1000
    last = "none"
    while waited <= timeout_ms:
        if _NOT_FOUND in page.inner_text("body"):
            return "not-in-arc"
        n = _visible_block_buttons(page)
        last = "one" if n == 1 else ("many" if n > 1 else "none")
        # Require the single-card state to hold across two polls so a list
        # that is mid-refresh can't pass on a transient count of one.
        if last == "one":
            page.wait_for_timeout(step)
            waited += step
            if _NOT_FOUND in page.inner_text("body"):
                return "not-in-arc"
            if _visible_block_buttons(page) == 1:
                return "one"
            continue
        page.wait_for_timeout(step)
        waited += step
    return last


def block_in_arc(page, gpt_id: str, network_id: str) -> str:
    """Filter the ARC by `Ad response ID: <gpt_id>` and block the matching
    card — only when it can prove the card is the one Confiant flagged.
    Returns one of:

      "blocked"      — exactly one card matched, it isn't a deal ad, and the
                       click went through; the caller records the block
      "not-in-arc"   — ARC showed "Couldn't find matching ad" (commonly:
                       Confiant RTB caught it upstream, or Open Bidding)
      "ambiguous"    — more than one ad on screen after filtering, i.e. the
                       filter didn't apply. Refused: clicking here is what
                       blocked unflagged Apple PG creatives in 2026-09
      "deal-ad"      — the matched card is a PG / preferred-deal ad. Refused:
                       those need a human decision
      "unconfirmed"  — the card didn't show the response ID we filtered on
      "no-block-btn" — no card rendered even after the reload trick

    Anything other than "blocked" means nothing was clicked.
    """
    page.goto(arc_url(network_id), wait_until="load", timeout=60000)
    page.wait_for_timeout(7000)
    apply_ad_response_id_filter(page, gpt_id)

    state = _wait_for_filtered_result(page, timeout_ms=20000)
    if state == "none":
        # Skeleton-stuck low-impression cards: reload. GAM persists the filter
        # in the URL hash (&as=…) — but if it doesn't, the reload lands on the
        # unfiltered feed, which the "many" check below refuses.
        page.reload(wait_until="load")
        page.wait_for_timeout(15000)
        state = _wait_for_filtered_result(page, timeout_ms=20000)

    if state == "not-in-arc":
        return "not-in-arc"
    if state == "many":
        return "ambiguous"
    if state != "one":
        return "no-block-btn"

    card_text = page.inner_text("body")
    if gpt_id not in card_text:
        return "unconfirmed"
    if any(m in card_text.lower() for m in _DEAL_MARKERS):
        return "deal-ad"

    btn = page.locator(_BLOCK_BTN).first
    try:
        btn.hover()
        page.wait_for_timeout(800)
    except Exception:
        pass
    btn.click()
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
