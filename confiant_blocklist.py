"""
confiant_blocklist — weekly job that pulls Confiant's flagged-ad data via
their REST API, picks the Google-served Security-category creatives, and
appends their landing-page domains to a GAM Protection's Advertiser URLs
blocklist.

Why this is a local-only script rather than a GitHub Actions cron:
GAM does not expose Protections via API. Driving the GAM UI with Playwright
requires a logged-in Google session with 2FA, which can't be done from a
headless CI runner. See docs/confiant_blocklist.md for the full design note.

Typical use (API mode — pulls last 7 days from Confiant):
  python confiant_blocklist.py \\
      --protection-id 28044902 --protection-label Everything --dry-run

Real run (remove --dry-run; browser will open):
  python confiant_blocklist.py --protection-id 28044902

Manual CSV mode (fallback if Confiant API is down):
  python confiant_blocklist.py --csv ~/Downloads/Alert\\ Log\\ CSV.csv \\
      --protection-id 28044902

Other modes:
  --inspect          Open the GAM Protections page in a visible browser, wait.
  --print-existing   Dump the current Advertiser URLs for the target Protection.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from html import escape
from pathlib import Path

import confiant_client
from gam_blocklist_ui import GAMBlocklistBrowser, default_profile_dir


# ── env / state setup ─────────────────────────────────────────────────────────

def _load_dotenv() -> None:
    env_file = Path(__file__).parent / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _state_path() -> Path:
    return Path(os.environ.get(
        "CONFIANT_BLOCKLIST_STATE",
        "~/.confiant-blocklist/state.sqlite",
    )).expanduser()


def _open_state() -> sqlite3.Connection:
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS blocked_domains (
            domain              TEXT PRIMARY KEY,
            issue_type          TEXT NOT NULL,
            first_seen_in_csv   TEXT NOT NULL,
            first_pushed_to_gam TEXT NOT NULL,
            protection_id       INTEGER NOT NULL,
            protection_label    TEXT
        );
        CREATE TABLE IF NOT EXISTS runs (
            run_id               INTEGER PRIMARY KEY AUTOINCREMENT,
            run_at               TEXT NOT NULL,
            source               TEXT,
            categories           TEXT,
            protection_id        INTEGER,
            protection_label     TEXT,
            total_google_rows    INTEGER,
            blockable_domains    INTEGER,
            new_domains_added    INTEGER,
            cloaked_rows_skipped INTEGER,
            dry_run              INTEGER,
            status               TEXT,
            error                TEXT
        );
    """)
    return conn


# ── main flow ─────────────────────────────────────────────────────────────────

@dataclass
class ArcStats:
    """Phase 2 result: GAM Ad Review Center manual-block run for the
    Cloaked rows whose Detail = 'ID xxxxx' (no destination URL)."""
    attempted: int = 0
    blocked: int = 0
    skipped_already: int = 0
    not_in_arc: int = 0
    errors: int = 0
    error_msg: str | None = None  # Phase-level fatal error (Playwright crash etc.)
    # Per-row outcomes. Tracking all three "handled" states (not just blocked)
    # lets the email rendering filter the Cloaked-review section so it doesn't
    # keep showing rows we already took care of.
    blocked_ids: list[tuple[str, str]] = None  # (confiant_id, gpt_id) for email
    skipped_cids: list[str] = None             # Confiant IDs we found already in state.sqlite
    not_in_arc_cids: list[str] = None          # Confiant IDs ARC said "Couldn't find" (likely OB-channel)
    errored_cids: list[str] = None             # Confiant IDs whose Phase 2 raised an error

    def __post_init__(self):
        if self.blocked_ids is None:
            self.blocked_ids = []
        if self.skipped_cids is None:
            self.skipped_cids = []
        if self.not_in_arc_cids is None:
            self.not_in_arc_cids = []
        if self.errored_cids is None:
            self.errored_cids = []

    @property
    def did_anything(self) -> bool:
        return self.attempted > 0

    @property
    def handled_cids(self) -> set[str]:
        """Confiant IDs Phase 2 took care of in any of the non-error outcomes
        (blocked just now, skipped because already done, not in ARC = handled
        upstream by Confiant RTB). The email rendering filters the
        Cloaked-review list against this set to avoid showing rows we don't
        actually need a human to look at."""
        return ({cid for cid, _ in self.blocked_ids}
                | set(self.skipped_cids)
                | set(self.not_in_arc_cids))


@dataclass
class RunSummary:
    source: str                # CSV path or 'api://<endpoint>/<hash>'
    categories: tuple[str, ...]
    protection_id: int
    protection_label: str
    total_google_rows: int
    blockable_domains_in_csv: int
    new_domains: list[tuple[str, str]]  # (domain, issue_type)
    skipped_already_blocked: int
    cloaked_for_review: list[confiant_client.FlaggedRow]
    dry_run: bool
    success: bool
    arc: ArcStats | None = None  # Phase 2 results (None if --no-arc or n/a)
    error: str | None = None


def diff_new_domains(
    conn: sqlite3.Connection,
    blockable: list[tuple[str, confiant_client.FlaggedRow]],
) -> tuple[list[tuple[str, str]], int]:
    """Return (new_domains_with_issue_type, count_already_known).

    new_domains_with_issue_type is unique by domain — if the same domain
    appears under multiple issue types in this CSV, the first wins.
    """
    seen_in_csv: dict[str, str] = {}
    for domain, row in blockable:
        seen_in_csv.setdefault(domain, row.issue_type)

    existing = {
        d for (d,) in conn.execute(
            f"SELECT domain FROM blocked_domains WHERE domain IN ({','.join('?' * len(seen_in_csv))})",
            list(seen_in_csv.keys()),
        )
    } if seen_in_csv else set()

    new = [(d, t) for d, t in seen_in_csv.items() if d not in existing]
    return new, len(seen_in_csv) - len(new)


def push_and_record(
    conn: sqlite3.Connection,
    summary: RunSummary,
    profile_dir: Path,
    headless: bool,
    debug: bool,
    network_id: str,
) -> None:
    """Push new domains via Playwright, then record them in state.

    State is updated only on successful UI save (Playwright raises on save
    failure). The full batch is recorded together so partial pushes don't
    leave state out of sync with GAM.
    """
    browser = GAMBlocklistBrowser(
        profile_dir=profile_dir,
        network_id=network_id,
        headless=headless,
        debug=debug,
    )
    browser.append_to_protection(summary.protection_id, [d for d, _ in summary.new_domains])

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    today_date = now[:10]
    conn.executemany(
        """INSERT OR IGNORE INTO blocked_domains
           (domain, issue_type, first_seen_in_csv, first_pushed_to_gam,
            protection_id, protection_label)
           VALUES (?, ?, ?, ?, ?, ?)""",
        [(d, t, today_date, now, summary.protection_id, summary.protection_label)
         for d, t in summary.new_domains],
    )
    conn.commit()


def run_arc_phase(
    cloaked_for_review: list[confiant_client.FlaggedRow],
    profile_dir: Path,
    network_id: str,
    headless: bool = False,
) -> ArcStats:
    """Phase 2 of the daily cron — walk Cloaked rows where Detail = 'ID xxxxx'
    (no destination URL exposed by Confiant's API), pull the GPT Ad Response ID
    from each Confiant adtrace page, then filter+block in GAM Ad Review Center.

    Failure isolation: any exception in the phase is captured into ArcStats.
    error_msg and printed to stderr — never raised. The URL-push results
    from Phase 1 stay valid even if ARC blows up entirely.

    Skips: rows whose GPT ID is already recorded in state.sqlite as
    `gam-arc:<gpt_id>` (i.e. we already blocked it on an earlier day, or via
    the standalone `scripts/confiant_gam_arc_block.py`).
    """
    import gam_arc

    stats = ArcStats()
    # Find Cloaked rows with Detail = "ID xxxxx" — dedupe by Confiant ID
    id_only: dict[str, confiant_client.FlaggedRow] = {}
    for r in cloaked_for_review:
        if not gam_arc.CLOAKED_ID_RE.match(r.detail.strip()):
            continue
        cid = r.detail.replace("ID ", "").strip()
        # Keep the highest-impression occurrence (best signal for adtrace pull)
        prev = id_only.get(cid)
        if prev is None or r.flagged_impressions > prev.flagged_impressions:
            id_only[cid] = r
    if not id_only:
        return stats  # nothing to do; KPI tile renders "0"

    # Filter against state.sqlite — skip GPT IDs we've already ARC-blocked.
    # The skip happens AFTER we pull the GPT ID per-Confiant-ID; we can't
    # skip by Confiant ID because state.sqlite is keyed by GPT ID.
    already_arc = gam_arc.already_arc_blocked(_state_path())

    print(f"\n=== ARC Phase 2: {len(id_only)} cloaked-by-ID candidate(s) ===",
          file=sys.stderr)
    try:
        from playwright.sync_api import sync_playwright
    except Exception as e:
        stats.error_msg = f"Playwright import failed: {e}"
        print(f"ARC phase skipped: {stats.error_msg}", file=sys.stderr)
        return stats

    try:
        with sync_playwright() as p:
            ctx = p.chromium.launch_persistent_context(
                str(profile_dir), headless=headless,
                viewport={"width": 1500, "height": 950},
            )
            confiant_page = ctx.pages[0] if ctx.pages else ctx.new_page()

            for cid, row in id_only.items():
                stats.attempted += 1
                # Phase 1: pull GPT ID from Confiant adtrace
                try:
                    confiant_page.goto(row.adtrace_url, wait_until="load",
                                       timeout=60000)
                    confiant_page.wait_for_timeout(5000)
                    gpt_id = gam_arc.extract_gpt_id(confiant_page)
                except Exception as e:
                    stats.errors += 1
                    stats.errored_cids.append(cid)
                    print(f"  Confiant {cid}: adtrace fetch failed: {e}",
                          file=sys.stderr)
                    continue
                if not gpt_id:
                    stats.errors += 1
                    stats.errored_cids.append(cid)
                    print(f"  Confiant {cid}: GPT Ad Response ID not found "
                          f"in adtrace body", file=sys.stderr)
                    continue
                if gpt_id in already_arc:
                    stats.skipped_already += 1
                    stats.skipped_cids.append(cid)
                    print(f"  Confiant {cid}: already ARC-blocked "
                          f"(gam-arc:{gpt_id[:12]}...) — skip", file=sys.stderr)
                    continue

                # Phase 2: ARC filter + block (fresh tab so filter chips don't
                # leak between iterations — same gotcha that bit the manual
                # script's first multi-iteration pass on 2026-06-17).
                arc_page = ctx.new_page()
                try:
                    status = gam_arc.block_in_arc(arc_page, gpt_id, network_id)
                except Exception as e:
                    stats.errors += 1
                    stats.errored_cids.append(cid)
                    print(f"  Confiant {cid}: ARC block raised: {e}",
                          file=sys.stderr)
                    arc_page.close()
                    continue
                arc_page.close()

                if status == "blocked":
                    stats.blocked += 1
                    stats.blocked_ids.append((cid, gpt_id))
                    gam_arc.record_arc_block(_state_path(), gpt_id, cid)
                    print(f"  Confiant {cid} → GPT {gpt_id[:12]}... → BLOCKED",
                          file=sys.stderr)
                elif status == "not-in-arc":
                    stats.not_in_arc += 1
                    stats.not_in_arc_cids.append(cid)
                    print(f"  Confiant {cid}: not in ARC (likely Open Bidding "
                          f"channel — see Thomas reply pending)", file=sys.stderr)
                else:
                    stats.errors += 1
                    stats.errored_cids.append(cid)
                    print(f"  Confiant {cid}: {status}", file=sys.stderr)

            ctx.close()
    except Exception as e:
        stats.error_msg = f"{type(e).__name__}: {e}"
        print(f"ARC phase fatal: {stats.error_msg}", file=sys.stderr)

    print(f"ARC phase done: attempted={stats.attempted} blocked={stats.blocked} "
          f"skipped_already={stats.skipped_already} not_in_arc={stats.not_in_arc} "
          f"errors={stats.errors}", file=sys.stderr)
    return stats


def record_run(conn: sqlite3.Connection, summary: RunSummary) -> None:
    conn.execute(
        """INSERT INTO runs
           (run_at, source, categories, protection_id, protection_label,
            total_google_rows, blockable_domains, new_domains_added,
            cloaked_rows_skipped, dry_run, status, error)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            datetime.now(timezone.utc).isoformat(timespec="seconds"),
            summary.source,
            ",".join(summary.categories),
            summary.protection_id,
            summary.protection_label,
            summary.total_google_rows,
            summary.blockable_domains_in_csv,
            len(summary.new_domains),
            len(summary.cloaked_for_review),
            int(summary.dry_run),
            "success" if summary.success else "failed",
            summary.error,
        ),
    )
    conn.commit()


# ── summary email ─────────────────────────────────────────────────────────────

# Brand palette — mirrors confiant_blocklist_weekly_report.py so the daily
# and weekly emails share one visual identity.
_NW_RED      = "#d72638"
_NW_GREEN    = "#22c55e"
_NW_AMBER    = "#f59e0b"
_NW_DARK     = "#1a1a1a"
_NW_MUTED    = "#6b7280"
_NW_BG_SOFT  = "#f0f4f8"
_NW_BG_LIGHT = "#f9fafb"
_NW_BORDER   = "#e1e5eb"


def _state_total() -> int:
    """Best-effort count of cumulative blocked domains for the KPI tile.
    Returns 0 if the state DB doesn't exist or is unreadable (don't fail
    the email send over a number)."""
    try:
        with sqlite3.connect(_state_path()) as c:
            return c.execute("SELECT COUNT(*) FROM blocked_domains").fetchone()[0]
    except Exception:
        return 0


def _arc_section_html(summary, dark, green, muted, bg_soft, bg_light, border, red) -> str:
    """Render the Phase 2 (GAM Ad Review Center) results into the daily email.
    Returns an empty string if the ARC phase didn't run / had nothing to do."""
    arc = summary.arc
    if arc is None or (not arc.did_anything and not arc.error_msg):
        return ""
    if arc.error_msg:
        return (
            f"<tr><td style='padding:8px 24px 0 24px'>"
            f"<h2 style='margin:14px 0 6px 0;color:{dark};font-size:15px;font-weight:700'>"
            f"GAM Ad Review Center &mdash; phase failed</h2>"
            f"<div style='background:#fff5f5;border:1px solid #f3c2c5;"
            f"border-left:4px solid {red};padding:12px 16px;border-radius:4px;"
            f"color:{dark};font-family:Menlo,Consolas,monospace;font-size:12px'>"
            f"{escape(arc.error_msg)}</div>"
            f"<p style='margin:8px 0 0 0;font-size:12px;color:{muted}'>"
            f"URL-push results above are unaffected. Re-run "
            f"<code>scripts/confiant_gam_arc_block.py</code> manually if needed."
            f"</p></td></tr>"
        )
    # Render the per-ID blocks if any
    if arc.blocked > 0:
        rows_html = "".join(
            f"<tr>"
            f"<td style='padding:6px 12px;border-bottom:1px solid {border};"
            f"font-family:Menlo,Consolas,monospace;font-size:12px;color:{dark}'>{cid}</td>"
            f"<td style='padding:6px 12px;border-bottom:1px solid {border};"
            f"font-family:Menlo,Consolas,monospace;font-size:11px;color:{dark};"
            f"word-break:break-all'>{gpt}</td>"
            f"</tr>" for cid, gpt in arc.blocked_ids
        )
        blocks_card = (
            f"<table role='presentation' cellpadding='0' cellspacing='0' border='0' "
            f"style='border-collapse:collapse;width:100%;border:1px solid {border};"
            f"border-radius:6px;overflow:hidden;margin:6px 0 14px 0'>"
            f"<thead><tr>"
            f"<th style='background:{bg_soft};padding:8px 12px;text-align:left;"
            f"font-size:11px;text-transform:uppercase;letter-spacing:0.5px;"
            f"color:{dark};border-bottom:1px solid {border}'>Confiant ID</th>"
            f"<th style='background:{bg_soft};padding:8px 12px;text-align:left;"
            f"font-size:11px;text-transform:uppercase;letter-spacing:0.5px;"
            f"color:{dark};border-bottom:1px solid {border}'>GPT Ad Response ID</th>"
            f"</tr></thead><tbody>{rows_html}</tbody></table>"
        )
    else:
        blocks_card = ""
    # Per-status counter line
    counters = []
    if arc.blocked: counters.append(f"<strong style='color:{green}'>{arc.blocked} blocked</strong>")
    if arc.skipped_already: counters.append(f"{arc.skipped_already} already done")
    if arc.not_in_arc: counters.append(f"{arc.not_in_arc} not in ARC")
    if arc.errors: counters.append(f"<strong style='color:{red}'>{arc.errors} errors</strong>")
    counter_line = " &middot; ".join(counters) if counters else ""

    return (
        f"<tr><td style='padding:8px 24px 0 24px'>"
        f"<h2 style='margin:14px 0 6px 0;color:{dark};font-size:15px;font-weight:700'>"
        f"GAM Ad Review Center &mdash; manual blocks ({arc.blocked})</h2>"
        f"<p style='margin:0 0 8px 0;color:{muted};font-size:12px'>"
        f"Attempted {arc.attempted} cloaked-by-ID candidate"
        f"{'s' if arc.attempted != 1 else ''} &middot; {counter_line}"
        f"</p>"
        f"{blocks_card}"
        f"<p style='margin:0 0 4px 0;color:{muted};font-size:11px;line-height:1.5'>"
        f"&ldquo;Not in ARC&rdquo; rows are typically served by Open Bidding "
        f"partners that GAM doesn't surface in its review center. Confiant's "
        f"Active Blocking handles those upstream &mdash; confirmed via "
        f"<code>providers_by_day</code> Blocking Status column."
        f"</p></td></tr>"
    )


def _build_email_html(summary: RunSummary) -> str:
    from datetime import date
    import re

    new_count = len(summary.new_domains)

    # Filter cloaked-review against Phase 2 results: rows whose Confiant ID
    # Phase 2 already handled (blocked / skipped because already in state /
    # not in ARC = Confiant RTB handled upstream) don't need to clutter the
    # "Cloaked — manual review needed" section. We only flag rows Phase 2
    # genuinely couldn't take care of (errors, or rows whose Detail isn't
    # the "ID xxxxx" shape Phase 2 targets).
    _id_pat = re.compile(r"^ID\s+\d+$", re.IGNORECASE)
    arc_handled: set[str] = summary.arc.handled_cids if summary.arc else set()
    cloaked_for_email: list = []
    for r in summary.cloaked_for_review:
        m = _id_pat.match(r.detail.strip())
        if m:
            cid = m.group().replace("ID ", "").strip()
            if cid in arc_handled:
                continue  # Phase 2 took care of it
        cloaked_for_email.append(r)
    cloaked_count = len(cloaked_for_email)

    cumulative = _state_total()
    today_str = date.today().strftime("%B %-d, %Y")

    # Status drives header colour + eyebrow text.
    if summary.dry_run:
        status_label = "Dry run"
        status_color = _NW_AMBER
    elif summary.success:
        status_label = "Success"
        status_color = _NW_GREEN
    else:
        status_label = "Failed"
        status_color = _NW_RED

    # ── KPI tile strip (4 cards, table-laid-out for Outlook safety) ──────
    def _kpi(label: str, value: str, accent: str = _NW_DARK) -> str:
        return (
            f"<td valign='top' style='background:{_NW_BG_SOFT};border:1px solid {_NW_BORDER};"
            f"padding:14px 16px;border-radius:6px;width:25%'>"
            f"<div style='font-size:11px;color:{_NW_MUTED};text-transform:uppercase;"
            f"letter-spacing:0.5px;font-weight:600;margin-bottom:6px'>{label}</div>"
            f"<div style='font-size:24px;color:{accent};font-weight:700;line-height:1.1'>"
            f"{value}</div></td>"
        )

    new_tile_color = _NW_RED if (new_count and not summary.success) else (
        _NW_GREEN if new_count else _NW_DARK
    )
    spacer = "<td style='width:8px;font-size:0;line-height:0'>&nbsp;</td>"
    arc_blocked = summary.arc.blocked if summary.arc else 0
    arc_color = _NW_GREEN if arc_blocked else _NW_DARK
    kpi_strip = (
        f"<table role='presentation' cellpadding='0' cellspacing='0' border='0' "
        f"style='border-collapse:separate;width:100%;margin:18px 0 6px 0'>"
        f"<tr>"
        f"{_kpi('URL blocks', f'{new_count:,}', new_tile_color)}"
        f"{spacer}"
        f"{_kpi('ARC blocks', f'{arc_blocked:,}', arc_color)}"
        f"{spacer}"
        f"{_kpi('Cloaked (review)', f'{cloaked_count:,}', _NW_AMBER if cloaked_count else _NW_DARK)}"
        f"{spacer}"
        f"{_kpi('Total blocklist', f'{cumulative:,}')}"
        f"</tr></table>"
    )

    # ── Error callout (only when summary.error is set) ──────────────────
    error_block = ""
    if summary.error:
        error_block = (
            f"<div style='background:#fff5f5;border:1px solid #f3c2c5;border-left:4px solid {_NW_RED};"
            f"padding:14px 18px;border-radius:4px;margin:14px 0;color:{_NW_DARK}'>"
            f"<div style='font-size:13px;font-weight:700;margin-bottom:4px'>Run failed</div>"
            f"<div style='font-family:Menlo,Consolas,monospace;font-size:12px;color:{_NW_DARK};"
            f"white-space:pre-wrap;word-break:break-word'>{summary.error}</div>"
            f"<p style='margin:10px 0 0 0;font-size:12px;color:{_NW_MUTED}'>"
            f"State.sqlite was not updated for the rows above — they'll be retried on the next run."
            f"</p></div>"
        )

    # ── New domains card (group by issue type, ordered by count) ─────────
    def _new_domains_card() -> str:
        if not summary.new_domains:
            empty_msg = "No new URLs to block" + (
                " — every Confiant-flagged Google Security domain in this run was already on the GAM Protection."
                if not summary.error else " in this run."
            )
            return (
                f"<div style='background:{_NW_BG_LIGHT};border:1px solid {_NW_BORDER};"
                f"border-left:4px solid {_NW_GREEN};padding:14px 18px;border-radius:4px;"
                f"margin:0 0 14px 0;color:{_NW_DARK};font-size:13px'>{empty_msg}</div>"
            )
        by_type: dict[str, list[tuple[str, str]]] = {}
        for d, t in summary.new_domains:
            by_type.setdefault(t, []).append((d, t))
        sections = ""
        for it in sorted(by_type, key=lambda k: -len(by_type[k])):
            rows_html = "".join(
                f"<tr><td style='padding:6px 12px;border-bottom:1px solid {_NW_BORDER};"
                f"font-family:Menlo,Consolas,monospace;font-size:12px;color:{_NW_DARK};"
                f"word-break:break-all'>{d}</td></tr>"
                for d, _ in sorted(by_type[it])
            )
            sections += (
                f"<table role='presentation' cellpadding='0' cellspacing='0' border='0' "
                f"style='border-collapse:collapse;width:100%;margin:0 0 12px 0;"
                f"border:1px solid {_NW_BORDER};border-radius:6px;overflow:hidden'>"
                f"<tr><td style='background:{_NW_BG_LIGHT};padding:9px 14px;"
                f"border-bottom:1px solid {_NW_BORDER}'>"
                f"<table role='presentation' cellpadding='0' cellspacing='0' border='0' "
                f"style='width:100%'><tr>"
                f"<td style='font-size:13px;font-weight:600;color:{_NW_DARK}'>{it}</td>"
                f"<td style='text-align:right;font-size:11px;font-weight:700;color:#ffffff;"
                f"background:{_NW_RED};padding:3px 10px;border-radius:10px;width:1px;"
                f"white-space:nowrap'>{len(by_type[it])} URLs</td></tr></table></td></tr>"
                f"<tr><td style='padding:0'>"
                f"<table role='presentation' cellpadding='0' cellspacing='0' border='0' "
                f"style='border-collapse:collapse;width:100%'>"
                f"<tbody>{rows_html}</tbody></table></td></tr></table>"
            )
        return sections

    # ── Cloaked review card — top N by impressions ──────────────────────
    def _cloaked_card() -> str:
        if not cloaked_for_email:
            handled_note = ""
            if summary.cloaked_for_review and not cloaked_for_email:
                # Every cloaked row got handled by Phase 2 (blocked / already
                # in state / not-in-arc). Surface that explicitly — otherwise
                # the green callout reads like a misleading "nothing was here".
                handled_note = (
                    f" All {len(summary.cloaked_for_review)} cloaked row"
                    f"{'s' if len(summary.cloaked_for_review) != 1 else ''} "
                    f"in this run were handled by Phase 2 above."
                )
            return (
                f"<div style='background:{_NW_BG_LIGHT};border:1px solid {_NW_BORDER};"
                f"border-left:4px solid {_NW_GREEN};padding:14px 18px;border-radius:4px;"
                f"margin:0 0 14px 0;color:{_NW_DARK};font-size:13px'>"
                f"No cloaked Google creatives need human review.{handled_note}</div>"
            )
        # Sort by impressions desc, cap at 20 to keep the email scannable.
        # Confiant trace URL is the source of truth — link out for full detail.
        rows_sorted = sorted(
            cloaked_for_email,
            key=lambda r: -r.flagged_impressions,
        )
        head = rows_sorted[:20]
        more = len(rows_sorted) - len(head)

        items_html = "".join(
            f"<tr>"
            f"<td style='padding:6px 12px;border-bottom:1px solid {_NW_BORDER};font-size:12px;color:{_NW_DARK};"
            f"font-family:Menlo,Consolas,monospace;white-space:nowrap'>{r.detail}</td>"
            f"<td style='padding:6px 12px;border-bottom:1px solid {_NW_BORDER};font-size:12px;color:{_NW_DARK};"
            f"text-align:right;font-weight:600;white-space:nowrap'>{r.flagged_impressions:,}</td>"
            f"<td style='padding:6px 12px;border-bottom:1px solid {_NW_BORDER};font-size:12px;color:{_NW_MUTED};"
            f"white-space:nowrap'>{r.last_seen}</td>"
            f"<td style='padding:6px 12px;border-bottom:1px solid {_NW_BORDER};font-size:12px;color:#1a73e8;"
            f"text-align:right;white-space:nowrap'><a href='{r.adtrace_url}' style='color:#1a73e8'>open &rarr;</a></td>"
            f"</tr>"
            for r in head
        )
        more_html = (
            f"<p style='margin:6px 0 0 0;color:{_NW_MUTED};font-size:12px'>"
            f"&hellip; and {more} more, ordered by impressions. Full list ships in the weekly RevOps digest "
            f"and is queryable via the Confiant Alert Log.</p>"
            if more > 0 else ""
        )
        return (
            f"<p style='margin:0 0 10px 0;font-size:13px;color:{_NW_MUTED}'>"
            f"These Google creatives are cloaked &mdash; Confiant detected the violation but the "
            f"destination is hidden from its scanner. Can't be auto-blocked via URL list. "
            f"Top {len(head)} by impressions, with adtrace links for manual review:"
            f"</p>"
            f"<table role='presentation' cellpadding='0' cellspacing='0' border='0' "
            f"style='border-collapse:collapse;width:100%;border:1px solid {_NW_BORDER};border-radius:6px;"
            f"overflow:hidden;margin:0 0 6px 0'>"
            f"<thead><tr>"
            f"<th style='background:{_NW_BG_SOFT};padding:8px 12px;text-align:left;font-size:11px;"
            f"text-transform:uppercase;letter-spacing:0.5px;color:{_NW_DARK};border-bottom:1px solid {_NW_BORDER}'>Confiant ID</th>"
            f"<th style='background:{_NW_BG_SOFT};padding:8px 12px;text-align:right;font-size:11px;"
            f"text-transform:uppercase;letter-spacing:0.5px;color:{_NW_DARK};border-bottom:1px solid {_NW_BORDER};"
            f"white-space:nowrap'>Imps (7d)</th>"
            f"<th style='background:{_NW_BG_SOFT};padding:8px 12px;text-align:left;font-size:11px;"
            f"text-transform:uppercase;letter-spacing:0.5px;color:{_NW_DARK};border-bottom:1px solid {_NW_BORDER};"
            f"white-space:nowrap'>Last seen</th>"
            f"<th style='background:{_NW_BG_SOFT};padding:8px 12px;text-align:right;font-size:11px;"
            f"text-transform:uppercase;letter-spacing:0.5px;color:{_NW_DARK};border-bottom:1px solid {_NW_BORDER}'>Trace</th>"
            f"</tr></thead><tbody>{items_html}</tbody></table>"
            f"{more_html}"
        )

    # ── GAM Protection CTA button (only if we have a network id to link to) ─
    network_id = os.environ.get("GAM_NETWORK_ID", "").strip()
    cta_html = ""
    if network_id and summary.protection_id:
        gam_url = (
            f"https://admanager.google.com/{network_id}#delivery/protections/"
            f"detail/protection_id={summary.protection_id}"
        )
        cta_html = (
            f"<p style='margin:14px 0'>"
            f"<a href='{gam_url}' style='display:inline-block;background:{_NW_DARK};color:#ffffff;"
            f"padding:10px 18px;border-radius:4px;text-decoration:none;font-size:13px;font-weight:600'>"
            f"View Protection &ldquo;{summary.protection_label}&rdquo; in GAM &rarr;</a></p>"
        )

    return f"""\
<html><body style='margin:0;padding:0;background:#f5f6f8;color:#222;font-family:Arial,Helvetica,sans-serif;font-size:14px;line-height:1.55'>
<table role='presentation' cellpadding='0' cellspacing='0' border='0' style='background:#f5f6f8;width:100%;padding:20px 0'>
<tr><td align='center'>
<table role='presentation' cellpadding='0' cellspacing='0' border='0' style='background:#ffffff;width:100%;max-width:760px;border:1px solid {_NW_BORDER};border-radius:8px;overflow:hidden'>
  <tr><td style='background:{_NW_RED};height:4px;font-size:0;line-height:0'>&nbsp;</td></tr>
  <tr><td style='padding:20px 24px 0 24px'>
    <div style='font-size:11px;font-weight:700;color:{_NW_RED};text-transform:uppercase;letter-spacing:1.2px'>
      Newsweek &middot; Brand Safety
    </div>
    <h1 style='margin:6px 0 4px 0;color:{_NW_DARK};font-size:22px;font-weight:700;line-height:1.2'>
      GAM Blocklist &mdash; Daily Push
    </h1>
    <div style='color:{_NW_MUTED};font-size:13px'>
      {today_str} &middot; Status:
      <span style='color:{status_color};font-weight:700'>{status_label}</span>
      &middot; Protection: <strong>{summary.protection_label}</strong>
      <span style='color:#9aa0a6'>(ID {summary.protection_id})</span>
      &middot; Categories: <strong>{', '.join(summary.categories) or 'Security'}</strong>
    </div>
  </td></tr>

  <tr><td style='padding:0 24px'>{kpi_strip}</td></tr>

  {f"<tr><td style='padding:0 24px'>{error_block}</td></tr>" if error_block else ""}

  <tr><td style='padding:18px 24px 0 24px'>
    <h2 style='margin:0 0 6px 0;color:{_NW_DARK};font-size:15px;font-weight:700'>
      {("URLs that would be added" if summary.dry_run else "URLs added to GAM Protection")} ({new_count})
    </h2>
    {_new_domains_card()}
  </td></tr>

  {_arc_section_html(summary, _NW_DARK, _NW_GREEN, _NW_MUTED, _NW_BG_SOFT, _NW_BG_LIGHT, _NW_BORDER, _NW_RED)}

  <tr><td style='padding:8px 24px 0 24px'>
    <h2 style='margin:14px 0 6px 0;color:{_NW_DARK};font-size:15px;font-weight:700'>
      Cloaked &mdash; manual review needed ({cloaked_count})
    </h2>
    {_cloaked_card()}
  </td></tr>

  {f"<tr><td style='padding:0 24px 8px 24px'>{cta_html}</td></tr>" if cta_html else ""}

  <tr><td style='padding:18px 24px 22px 24px;background:{_NW_BG_LIGHT};border-top:1px solid {_NW_BORDER}'>
    <p style='margin:0;font-size:11px;color:#9aa0a6;line-height:1.5'>
      Generated by <code>confiant_blocklist.py</code> on
      {datetime.now().strftime('%Y-%m-%d %H:%M %Z').strip()}.
      Source: <code>{summary.source}</code>.
      State DB: <code>~/.confiant-blocklist/state.sqlite</code>.
    </p>
  </td></tr>
</table>
</td></tr></table>
</body></html>"""


def _send_email(summary: RunSummary) -> None:
    """Route the post-run email based on outcome.

    Two recipient env vars, deliberately separated so success/failure can
    go to different inboxes (or one can be paused without affecting the
    other):

    - `CONFIANT_REPORT_TO_EMAIL` — successful + dry-run summaries. Send a
      daily "here's what we blocked" digest to a brand-safety alias or
      a person who wants the steady drumbeat. Comment out / unset to
      silence success summaries entirely.
    - `CONFIANT_ALERT_TO_EMAIL` — failure-only alerts. Send these to the
      on-call human who'd fix the pipeline (refresh the Google session,
      repoint a selector, etc.).

    Quiet-on-success / loud-on-failure mode (introduced 2026-06-10): set
    `CONFIANT_ALERT_TO_EMAIL` and leave `CONFIANT_REPORT_TO_EMAIL` unset.
    """
    from agentmail import AgentMail
    from datetime import date

    api_key = os.environ.get("AGENTMAIL_API_KEY")
    inbox_id = os.environ.get("AGENTMAIL_INBOX_ID")
    summary_to = os.environ.get("CONFIANT_REPORT_TO_EMAIL") or os.environ.get("REPORT_TO_EMAIL")
    alert_to   = os.environ.get("CONFIANT_ALERT_TO_EMAIL")

    if not (api_key and inbox_id):
        print("Skipping email — AGENTMAIL_API_KEY / AGENTMAIL_INBOX_ID not set",
              file=sys.stderr)
        return

    # Pick recipient by outcome. Failures prefer CONFIANT_ALERT_TO_EMAIL but
    # fall back to the summary recipient if no dedicated alert address is
    # configured (so historic setups don't lose their failure email).
    is_failure = (not summary.dry_run) and (not summary.success)
    recipient = (alert_to or summary_to) if is_failure else summary_to

    if not recipient:
        # Common steady-state path: success email is paused on purpose.
        # Stay quiet there; only complain when a real failure has nowhere to go.
        if is_failure:
            print("Skipping FAILURE email — neither CONFIANT_ALERT_TO_EMAIL "
                  "nor CONFIANT_REPORT_TO_EMAIL is set", file=sys.stderr)
        return

    n = len(summary.new_domains)
    date_str = date.today().strftime("%b %-d")
    # Subject leads with the action + count so the inbox preview is useful
    # without opening: "GAM blocklist · 8 new URLs blocked — Jun 10".
    if summary.dry_run:
        subject = f"GAM blocklist · DRY RUN — would block {n} URL{'s' if n != 1 else ''} — {date_str}"
    elif is_failure:
        subject = f"GAM blocklist · FAILED — {date_str} (will retry)"
    elif n == 0:
        subject = f"GAM blocklist · no new URLs — {date_str}"
    else:
        subject = f"GAM blocklist · {n} new URL{'s' if n != 1 else ''} blocked — {date_str}"
    AgentMail(api_key=api_key).inboxes.messages.send(
        inbox_id,
        to=recipient,
        subject=subject,
        html=_build_email_html(summary),
    )
    print(f"{'Failure alert' if is_failure else 'Summary'} email sent to {recipient}")


# ── CLI ───────────────────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Append Confiant-flagged Google domains to a GAM Protection.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--inspect", action="store_true",
                     help="Open the GAM Protections page in a visible browser "
                          "and wait — used for first-time login and selector "
                          "verification. No CSV processed.")
    mode.add_argument("--print-existing", action="store_true",
                     help="Open the target Protection, print the current "
                          "Advertiser URLs to stdout, and exit. No CSV "
                          "processed, no modification. Use this before the "
                          "first real run to see what's already in the prod "
                          "Protection.")

    p.add_argument("--csv",
                   help="Path to a Confiant CSV (manual fallback). If omitted, "
                        "pulls the issue_type_by_domain report from the Confiant "
                        "API using CONFIANT_API_KEY.")
    p.add_argument("--api-days", type=int, default=7,
                   help="When pulling from the API, lookback window in days. "
                        "Default 7. Ignored if --csv is given.")
    p.add_argument("--categories", default="Security",
                   help="Comma-separated Issue Category values to block. "
                        "Default 'Security'. Pass 'Security,Quality' to also "
                        "block annoying-but-not-malicious creatives.")
    p.add_argument("--protection-id", type=int,
                   help="GAM Protection ID to append to (e.g. 28044902). "
                        "Required for normal runs and --print-existing.")
    p.add_argument("--protection-label", default=None,
                   help="Human-readable name for the Protection (e.g. "
                        "'Everything'). Used only for email subject lines and "
                        "state-table display; the script navigates by ID. "
                        "Defaults to 'Protection #<id>'.")
    p.add_argument("--dry-run", action="store_true",
                   help="Pull report, diff against state, send summary email, "
                        "but do not launch the browser or modify GAM.")
    p.add_argument("--headless", action="store_true",
                   help="Run Playwright headlessly. NOT recommended — Google "
                        "login may not survive headless mode.")
    p.add_argument("--debug", action="store_true",
                   help="Verbose logging + screenshots saved to the profile dir.")
    p.add_argument("--profile-dir", type=Path, default=None,
                   help="Override the Playwright persistent-profile directory. "
                        "Default: ~/.confiant-blocklist/playwright-profile")
    p.add_argument("--no-email", action="store_true",
                   help="Skip the summary email even if env vars are set.")
    p.add_argument("--no-arc", action="store_true",
                   help="Skip the GAM Ad Review Center Phase 2 (just push "
                        "URL flags to Protection and finish).")
    return p.parse_args()


def _label_for(args: argparse.Namespace) -> str:
    return args.protection_label or f"Protection #{args.protection_id}"


def _run_inspect(args, profile_dir, network_id) -> int:
    GAMBlocklistBrowser(profile_dir, network_id, headless=False, debug=True) \
        .inspect(protection_id=args.protection_id)
    return 0


def _run_print_existing(args, profile_dir, network_id) -> int:
    if not args.protection_id:
        print("--protection-id is required for --print-existing.", file=sys.stderr)
        return 2
    urls = GAMBlocklistBrowser(profile_dir, network_id,
                                headless=args.headless, debug=args.debug) \
        .read_existing_urls(args.protection_id)
    print(f"# {len(urls)} Advertiser URLs currently in "
          f"{_label_for(args)} (ID {args.protection_id}):")
    for u in urls:
        print(u)
    return 0


def main() -> int:
    _load_dotenv()
    args = _parse_args()
    profile_dir = args.profile_dir or default_profile_dir()
    network_id = os.environ.get("GAM_NETWORK_ID")

    if args.inspect or args.print_existing:
        if not network_id:
            print("GAM_NETWORK_ID env var required.", file=sys.stderr)
            return 2
        if args.inspect:
            return _run_inspect(args, profile_dir, network_id)
        return _run_print_existing(args, profile_dir, network_id)

    if not args.protection_id:
        print("--protection-id is required (or pass --inspect / --print-existing).",
              file=sys.stderr)
        return 2

    categories = tuple(c.strip() for c in args.categories.split(",") if c.strip())
    if args.csv:
        report = confiant_client.parse_csv(args.csv)
        print(f"Loaded Confiant CSV from {args.csv}")
    else:
        report = confiant_client.fetch_via_api(days=args.api_days)
        print(f"Pulled Confiant API report ({len(report.rows)} rows, "
              f"source={report.source})")

    blockable, cloaked = report.blockable_domains(categories=categories)
    print(f"Google rows ({'+'.join(categories)}): {len(report.google_rows)}  "
          f"blockable={len(blockable)}  cloaked={len(cloaked)}")

    conn = _open_state()
    new_domains, skipped = diff_new_domains(conn, blockable)
    print(f"New domains to push: {len(new_domains)}  "
          f"(already in state: {skipped})")

    summary = RunSummary(
        source=report.source,
        categories=categories,
        protection_id=args.protection_id,
        protection_label=_label_for(args),
        total_google_rows=len(report.google_rows),
        blockable_domains_in_csv=len({d for d, _ in blockable}),
        new_domains=new_domains,
        skipped_already_blocked=skipped,
        cloaked_for_review=cloaked,
        dry_run=args.dry_run,
        success=True,
    )

    if not args.dry_run and not network_id:
        summary.success = False
        summary.error = "GAM_NETWORK_ID env var not set"
    elif not args.dry_run and new_domains:
        try:
            push_and_record(
                conn=conn,
                summary=summary,
                profile_dir=profile_dir,
                headless=args.headless,
                debug=args.debug,
                network_id=network_id,
            )
        except Exception as e:
            summary.success = False
            summary.error = f"{type(e).__name__}: {e}"
            print(f"GAM push failed: {summary.error}", file=sys.stderr)

    # Phase 2: GAM Ad Review Center manual block for cloaked-by-ID rows.
    # Isolated from Phase 1 — any failure here is captured into ArcStats and
    # logged, but doesn't flip summary.success or alter the URL-push outcome.
    if not args.dry_run and not args.no_arc and network_id and cloaked:
        summary.arc = run_arc_phase(
            cloaked_for_review=cloaked,
            profile_dir=profile_dir,
            network_id=network_id,
            headless=args.headless,
        )

    record_run(conn, summary)
    if not args.no_email:
        _send_email(summary)

    return 0 if summary.success else 1


if __name__ == "__main__":
    sys.exit(main())
