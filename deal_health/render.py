"""
Outlook-safe HTML renderer for the weekly deal health email.

Pure function: render_email(payload) -> str. No I/O, no datetime.now(), no
globals consulted at call time. Layout is strict table-based — no flexbox,
no grid, no CSS variables, no <style> blocks, all styles inline. Every cell
that has a background also carries both `bgcolor=` AND inline
`background-color:` because Outlook strips one or the other depending on
context.
"""

from __future__ import annotations

import html
from datetime import date
from itertools import groupby
from typing import Iterable, Optional

from .colors import (
    BORDER_BLUE_LNK,
    BORDER_DEEP_BLUE,
    BORDER_GRAY,
    CHIP_PA_BG, CHIP_PA_TEXT,
    CHIP_PD_BG, CHIP_PD_TEXT,
    CHIP_PG_BG, CHIP_PG_TEXT,
    CONTENT_BG,
    EMAIL_MAX_WIDTH_PX,
    HEADER_BG,
    KPI_TILE_BG,
    METHODOLOGY_BG,
    PAGE_BG,
    SUBTLE_GRAY_BG,
    TEXT_DEFAULT,
    TEXT_FAINT,
    TEXT_HINT,
    TEXT_INVERSE,
    TEXT_MUTED,
    TEXT_PRIMARY,
    TINT_BLUE,
    TINT_PURPLE,
)
from .models import (
    AdvertiserRollup,
    Payload,
    SSPRollup,
    UnhealthyDeal,
)

# ── primitives ─────────────────────────────────────────────────────────────

FONT_STACK = (
    "-apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif"
)


def _esc(s: Optional[str]) -> str:
    return html.escape(s) if s else ""


def fmt_count(n: int) -> str:
    """Compact number — B/M/K with 1 decimal for B/M, no decimal for K,
    comma-separated below 1,000."""
    if n is None:
        return "—"
    n = int(n)
    if n >= 1_000_000_000:
        return f"{n / 1_000_000_000:.1f}B"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.0f}K"
    return f"{n:,}"


def fmt_pct(share: float) -> str:
    return f"{round(share * 100)}%"


def _cell(
    content: str,
    *,
    bg: Optional[str] = None,
    style: str = "",
    align: Optional[str] = None,
    valign: Optional[str] = None,
    colspan: Optional[int] = None,
    width: Optional[str] = None,
) -> str:
    """Single <td> emitter. If `bg` is set, both `bgcolor=` and inline
    `background-color:` are emitted (Outlook compat)."""
    if bg and "background-color" not in style:
        style = f"background-color:{bg}; {style}".strip()
    bg_attr     = f' bgcolor="{bg}"'      if bg          else ""
    style_attr  = f' style="{style}"'     if style       else ""
    align_attr  = f' align="{align}"'     if align       else ""
    valign_attr = f' valign="{valign}"'   if valign      else ""
    colspan_attr= f' colspan="{colspan}"' if colspan     else ""
    width_attr  = f' width="{width}"'     if width       else ""
    return (
        f'<td{bg_attr}{align_attr}{valign_attr}{colspan_attr}{width_attr}{style_attr}>'
        f"{content}</td>"
    )


def _row(*cells: str) -> str:
    return "<tr>" + "".join(cells) + "</tr>"


def _table(
    inner: str,
    *,
    bg: Optional[str] = None,
    width: str = "100%",
    extra_style: str = "",
) -> str:
    style = f"border-collapse:collapse;"
    if bg:
        style += f" background-color:{bg};"
    if extra_style:
        style += " " + extra_style
    bg_attr = f' bgcolor="{bg}"' if bg else ""
    return (
        f'<table cellpadding="0" cellspacing="0" border="0" width="{width}"'
        f'{bg_attr} style="{style}">{inner}</table>'
    )


def _chip(deal_type: Optional[str]) -> str:
    """Deal-type pill (PA blue / PD purple / PG green)."""
    if deal_type == "PA":
        bg, tx = CHIP_PA_BG, CHIP_PA_TEXT
        label = "PA"
    elif deal_type == "PD":
        bg, tx = CHIP_PD_BG, CHIP_PD_TEXT
        label = "PD"
    elif deal_type == "PG":
        bg, tx = CHIP_PG_BG, CHIP_PG_TEXT
        label = "PG"
    else:
        bg, tx = SUBTLE_GRAY_BG, TEXT_MUTED
        label = "—"
    return (
        f'<span style="display:inline-block; background-color:{bg}; color:{tx};'
        f' font-family:Menlo, Consolas, monospace; font-size:11px; font-weight:600;'
        f' padding:2px 8px; border-radius:3px; letter-spacing:0.4px;">{label}</span>'
    )


# ── section: black header ──────────────────────────────────────────────────

def _section_header(report_date: date) -> str:
    inner = _row(
        _cell(
            f'<div style="font-size:11px; letter-spacing:1.2px; color:{TEXT_HINT};'
            f' text-transform:uppercase; font-weight:600;">Newsweek &nbsp;&middot;&nbsp; Weekly Deal Health</div>'
            f'<div style="font-size:24px; color:{TEXT_INVERSE}; font-weight:700;'
            f' margin-top:6px;">{report_date.strftime("%B %d, %Y")}</div>'
            f'<div style="font-size:13px; color:{TEXT_HINT}; margin-top:8px;">Unhealthy PMP &amp; PG deals</div>',
            bg=HEADER_BG,
            style=f"padding:28px 28px 20px 28px; font-family:{FONT_STACK};",
        )
    )
    return _row(_cell(_table(inner, bg=HEADER_BG), bg=CONTENT_BG, style="padding:0;"))


# ── section: 3 KPI tiles ───────────────────────────────────────────────────

def _kpi_tile(label: str, value: str, sub: str, value_color: str = TEXT_PRIMARY) -> str:
    return _cell(
        _table(
            _row(_cell(
                f'<div style="font-size:11px; color:{TEXT_MUTED}; text-transform:uppercase;'
                f' letter-spacing:0.8px; font-weight:600;">{_esc(label)}</div>'
                f'<div style="font-size:30px; color:{value_color}; font-weight:700; margin-top:8px;">{_esc(value)}</div>'
                f'<div style="font-size:12px; color:{TEXT_HINT}; margin-top:6px;">{_esc(sub)}</div>',
                bg=KPI_TILE_BG,
                style=f"padding:18px 20px; border:1px solid {BORDER_GRAY};"
                      f" border-radius:6px; font-family:{FONT_STACK};",
            )),
            bg=KPI_TILE_BG,
        ),
        bg=CONTENT_BG,
        style="padding:0 8px;",
        valign="top",
        width="33%",
    )


def _section_kpis(p: Payload) -> str:
    top_dsp_name, top_dsp_share = p.top_dsp
    inner = _row(
        _kpi_tile(
            "Unhealthy deals",
            f"{p.total_deals:,}",
            f"across {p.seller_count} sellers",
        ),
        _kpi_tile(
            "Bid requests unanswered",
            fmt_count(p.total_bid_requests),
            f"over last {p.lookback_days} days",
        ),
        _kpi_tile(
            "Top DSP concentration",
            fmt_pct(top_dsp_share),
            f"on {_esc(top_dsp_name)}",
        ),
    )
    return _row(
        _cell(
            _table(inner, bg=CONTENT_BG),
            bg=CONTENT_BG,
            style="padding:20px 20px 8px 20px;",
        )
    )


# ── section: executive summary ─────────────────────────────────────────────

def _section_summary(p: Payload) -> str:
    top_dsp_name, top_dsp_share = p.top_dsp
    top_ssp = p.by_ssp[0] if p.by_ssp else None
    top_adv = p.top_dark_advertisers[0] if p.top_dark_advertisers else None

    top_ssp_html = (
        f'<strong style="color:{TEXT_PRIMARY};">{_esc(top_ssp.ssp)}</strong>'
        f' carries {fmt_pct(top_ssp.share_of_total)} on the supply side. '
        if top_ssp else ""
    )
    top_adv_html = (
        f'Top advertiser by silent volume: '
        f'<strong style="color:{TEXT_PRIMARY};">{_esc(top_adv.advertiser)}</strong>'
        f' ({fmt_count(top_adv.bid_requests)} requests across {top_adv.deal_count} deals). '
        if top_adv else ""
    )
    # Find seller with longest list (deal count desc, House/Unknown excluded).
    seller_counts: dict[str, int] = {}
    for d in p.deals:
        s = d.parsed.seller
        if s in ("House", "Unknown"):
            continue
        seller_counts[s] = seller_counts.get(s, 0) + 1
    longest_seller_html = ""
    if seller_counts:
        seller, ct = max(seller_counts.items(), key=lambda kv: kv[1])
        longest_seller_html = (
            f'<strong style="color:{TEXT_PRIMARY};">{_esc(seller)}</strong>'
            f' has the longest list ({ct} deals). '
        )

    summary_html = (
        f'<div style="font-size:11px; color:{TEXT_MUTED}; letter-spacing:0.8px;'
        f' text-transform:uppercase; font-weight:600;">Executive summary</div>'
        f'<div style="font-size:14px; color:{TEXT_DEFAULT}; line-height:1.55; margin-top:8px;">'
        f'{p.total_deals:,} deals across {p.seller_count} sellers received '
        f'<strong style="color:{TEXT_PRIMARY};">{fmt_count(p.total_bid_requests)}</strong> '
        f'bid requests in the last {p.lookback_days} days but produced no bids '
        f'&mdash; almost entirely a buyer-side activation issue. '
        f'<strong style="color:{TEXT_PRIMARY};">{_esc(top_dsp_name)}</strong> '
        f'accounts for {fmt_pct(top_dsp_share)} of unanswered requests; '
        f'{top_ssp_html}'
        f'{top_adv_html}'
        f'{longest_seller_html}'
        f'Sellers below are ordered by deal count, with each book broken out by '
        f'SSP &rarr; DSP &rarr; Agency &rarr; Advertiser so the dark spots are easy to spot.'
        f'</div>'
    )
    return _row(
        _cell(
            summary_html,
            bg=CONTENT_BG,
            style=f"padding:18px 28px 12px 28px; font-family:{FONT_STACK};",
        )
    )


# ── section: side-by-side breakdown tables (By SSP + Top dark advertisers) ─

def _ssp_row(r: SSPRollup) -> str:
    return _row(
        _cell(
            f'<div style="font-size:13px; color:{TEXT_PRIMARY}; font-weight:600;">{_esc(r.ssp)}</div>'
            f'<div style="font-size:11px; color:{TEXT_MUTED}; margin-top:2px;">{r.deal_count} deals</div>',
            bg=CONTENT_BG,
            style=f"padding:10px 12px; border-bottom:1px solid {BORDER_GRAY};",
        ),
        _cell(
            f'<div style="font-size:13px; color:{TEXT_PRIMARY}; font-weight:600;">{fmt_count(r.bid_requests)}</div>'
            f'<div style="font-size:11px; color:{TEXT_MUTED}; margin-top:2px;">{fmt_pct(r.share_of_total)}</div>',
            bg=CONTENT_BG,
            align="right",
            style=f"padding:10px 12px; border-bottom:1px solid {BORDER_GRAY};",
        ),
    )


def _adv_row(r: AdvertiserRollup) -> str:
    return _row(
        _cell(
            f'<div style="font-size:13px; color:{TEXT_PRIMARY}; font-weight:600;">{_esc(r.advertiser)}</div>'
            f'<div style="font-size:11px; color:{TEXT_MUTED}; margin-top:2px;">{r.deal_count} deals</div>',
            bg=CONTENT_BG,
            style=f"padding:10px 12px; border-bottom:1px solid {BORDER_GRAY};",
        ),
        _cell(
            f'<div style="font-size:13px; color:{TEXT_PRIMARY}; font-weight:600;">{fmt_count(r.bid_requests)}</div>',
            bg=CONTENT_BG,
            align="right",
            style=f"padding:10px 12px; border-bottom:1px solid {BORDER_GRAY};",
        ),
    )


def _breakdown_table(title: str, rows_html: str) -> str:
    title_html = (
        f'<div style="font-size:11px; color:{TEXT_MUTED}; letter-spacing:0.8px;'
        f' text-transform:uppercase; font-weight:600; margin-bottom:8px;">{_esc(title)}</div>'
    )
    return (
        title_html
        + _table(rows_html, bg=CONTENT_BG, extra_style=f"border:1px solid {BORDER_GRAY}; border-radius:4px;")
    )


def _section_breakdowns(p: Payload) -> str:
    ssp_rows = "".join(_ssp_row(r) for r in p.by_ssp)
    adv_rows = "".join(_adv_row(r) for r in p.top_dark_advertisers)

    inner = _row(
        _cell(_breakdown_table("By SSP", ssp_rows),
              bg=CONTENT_BG, valign="top", width="50%",
              style="padding-right:10px;"),
        _cell(_breakdown_table("Top dark advertisers", adv_rows),
              bg=CONTENT_BG, valign="top", width="50%",
              style="padding-left:10px;"),
    )
    return _row(
        _cell(_table(inner, bg=CONTENT_BG),
              bg=CONTENT_BG, style="padding:14px 28px 8px 28px;")
    )


# ── section: CTA card ──────────────────────────────────────────────────────

def _button(text: str, href: str, *, primary: bool) -> str:
    """Bullet-proof table-button anchor — works in Outlook desktop + web."""
    if primary:
        bg, fg, border = BORDER_BLUE_LNK, TEXT_INVERSE, BORDER_DEEP_BLUE
    else:
        bg, fg, border = CONTENT_BG, BORDER_BLUE_LNK, BORDER_BLUE_LNK
    cell_style = (
        f"background-color:{bg}; border:1px solid {border}; border-radius:4px;"
        f" padding:10px 18px;"
    )
    return _table(
        _row(_cell(
            f'<a href="{_esc(href)}" style="color:{fg}; font-size:13px; font-weight:600;'
            f' text-decoration:none; font-family:{FONT_STACK};">{text}</a>',
            bg=bg,
            style=cell_style,
            align="center",
        )),
        bg=bg,
        width="auto",
    )


def _section_cta(p: Payload) -> str:
    inner = _row(
        _cell(
            f'<div style="font-size:13px; color:{TEXT_PRIMARY}; font-weight:600;">Need the full data?</div>'
            f'<div style="font-size:12px; color:{TEXT_MUTED}; margin-top:4px;">'
            f'All {p.total_deals:,} deals are inline below, ordered by seller. '
            f'For sortable/filterable access, use one of these:</div>',
            bg=SUBTLE_GRAY_BG,
            style="padding:0;",
            valign="middle",
            width="60%",
        ),
        _cell(
            _table(
                _row(
                    _cell(_button("&#x2B07; Download CSV", p.csv_url, primary=True),
                          bg=SUBTLE_GRAY_BG, style="padding-right:8px;"),
                    _cell(_button("Open dashboard &rarr;", p.dashboard_url, primary=False),
                          bg=SUBTLE_GRAY_BG, style="padding-left:0;"),
                ),
                bg=SUBTLE_GRAY_BG,
                width="auto",
            ),
            bg=SUBTLE_GRAY_BG,
            align="right",
            valign="middle",
            width="40%",
            style="padding:0;",
        ),
    )
    return _row(
        _cell(
            _table(inner, bg=SUBTLE_GRAY_BG,
                   extra_style=f"border:1px solid {BORDER_GRAY}; border-radius:6px;"),
            bg=CONTENT_BG,
            style="padding:14px 28px 16px 28px;",
        )
    )


# ── section: methodology callout ───────────────────────────────────────────

def _section_methodology(p: Payload) -> str:
    defect_count = sum(1 for d in p.deals if d.has_naming_defect)
    defect_note = (
        f' <strong style="color:{TEXT_DEFAULT};">{defect_count} deal'
        f'{"" if defect_count == 1 else "s"}</strong> have a deal-name SSP slot that '
        f'looks wrong (DSP or &ldquo;SSP&rdquo; placeholder in slot 3) &mdash; '
        f'they\'re still grouped under their real SSP above, but the names should '
        f'be flagged to AdOps for renaming.'
        if defect_count else ""
    )
    body = (
        f'<strong style="color:{TEXT_DEFAULT};">Methodology.</strong> '
        f'Auction deals (PA / PD) receiving bid requests but no bids over a '
        f'7-day window. Deals must have existed for at least 90 days (we don\'t '
        f'flag deals buyers haven\'t had time to wire up). GAM PD threshold: '
        f'&ge;7 days in data and &ge;100K bid requests (PDs have first-look '
        f'optionality so low-volume zero-bid deals are noise). GAM PA and Magnite '
        f'have no volume threshold. SSP attribution comes from the data source '
        f'(Magnite / GAM / Pubmatic), not the deal name &mdash; Pubmatic deals with '
        f'legacy naming still appear under Pubmatic.'
        f'{defect_note}'
    )
    callout = (
        f'<div style="font-size:11px; color:{TEXT_MUTED}; line-height:1.6;'
        f' padding:10px 14px; background-color:{METHODOLOGY_BG};'
        f' border-radius:4px; border-left:3px solid {BORDER_GRAY};"'
        f' bgcolor="{METHODOLOGY_BG}">{body}</div>'
    )
    return _row(
        _cell(callout, bg=CONTENT_BG, style="padding:4px 28px 16px 28px;")
    )


# ── section: per-seller hierarchy ──────────────────────────────────────────

def _format_deal_line(d: UnhealthyDeal) -> str:
    """Single deal row: chip + 'Geo · Format · Floor' + request count."""
    pieces: list[str] = []
    if d.parsed.geo:
        pieces.append(_esc(d.parsed.geo))
    if d.parsed.ad_format:
        pieces.append(_esc(d.parsed.ad_format))
    if d.parsed.floor:
        pieces.append(_esc(d.parsed.floor))
    middle = "  &middot;  ".join(pieces) or "&mdash;"

    return _row(
        _cell(
            _chip(d.parsed.deal_type),
            bg=CONTENT_BG,
            style="padding:5px 0 5px 18px; white-space:nowrap;",
            valign="middle",
            width="60",
        ),
        _cell(
            f'<span style="font-size:12px; color:{TEXT_DEFAULT};">{middle}</span>',
            bg=CONTENT_BG,
            style="padding:5px 8px;",
            valign="middle",
        ),
        _cell(
            f'<span style="font-size:12px; color:{TEXT_PRIMARY}; font-weight:600;'
            f' font-variant-numeric: tabular-nums;">{fmt_count(d.bid_requests)}</span>',
            bg=CONTENT_BG,
            align="right",
            valign="middle",
            style="padding:5px 18px 5px 8px; white-space:nowrap;",
        ),
    )


def _group_header(label_type: str, label_value: str, deal_count: int, requests: int,
                  *, indent_px: int, bg: str, text_color: str,
                  border_color: Optional[str] = None) -> str:
    """One-line header for an SSP/DSP/Advertiser grouping in the per-seller tree."""
    border = f"border-left:3px solid {border_color};" if border_color else ""
    label_html = (
        f'<span style="font-size:10px; color:{TEXT_MUTED}; text-transform:uppercase;'
        f' letter-spacing:0.8px; font-weight:600; margin-right:8px;">{_esc(label_type)}</span>'
        if label_type else ""
    )
    return _row(
        _cell(
            f'{label_html}<span style="font-size:13px; color:{text_color}; font-weight:600;">{_esc(label_value)}</span>',
            bg=bg,
            style=f"padding:6px 0 6px {indent_px}px; {border}",
            valign="middle",
        ),
        _cell("", bg=bg, style="padding:0;"),
        _cell(
            f'<span style="font-size:11px; color:{TEXT_MUTED}; font-variant-numeric: tabular-nums;">'
            f'{deal_count} {"deal" if deal_count == 1 else "deals"} &middot; {fmt_count(requests)} req</span>',
            bg=bg,
            align="right",
            valign="middle",
            style="padding:6px 18px 6px 8px; white-space:nowrap;",
        ),
    )


def _seller_section(seller: str, deals: list[UnhealthyDeal]) -> str:
    """One seller block. Hierarchy: SSP → DSP → (Advertiser/Agency) → deals.
    All sub-groups sorted by bid_requests descending."""
    total_requests = sum(d.bid_requests for d in deals)
    body_rows: list[str] = []

    # Seller-level header
    body_rows.append(_row(
        _cell(
            f'<div style="font-size:18px; color:{TEXT_PRIMARY}; font-weight:700;">{_esc(seller)}</div>'
            f'<div style="font-size:12px; color:{TEXT_MUTED}; margin-top:4px;">'
            f'{len(deals)} {"deal" if len(deals) == 1 else "deals"} &middot; '
            f'{fmt_count(total_requests)} bid requests unanswered</div>',
            bg=CONTENT_BG,
            colspan=3,
            style=f"padding:18px 18px 12px 18px; border-bottom:1px solid {BORDER_GRAY};",
        )
    ))

    # Group by source SSP (data source, ground truth), descending by sum
    by_ssp: dict[str, list[UnhealthyDeal]] = {}
    for d in deals:
        by_ssp.setdefault(d.source_ssp, []).append(d)

    for ssp_name in sorted(by_ssp, key=lambda s: -sum(d.bid_requests for d in by_ssp[s])):
        ssp_deals = by_ssp[ssp_name]
        body_rows.append(_group_header(
            "SSP", ssp_name, len(ssp_deals),
            sum(d.bid_requests for d in ssp_deals),
            indent_px=18, bg=TINT_PURPLE, text_color=TEXT_PRIMARY,
            border_color=BORDER_BLUE_LNK if ssp_name == "AdX" else None,
        ))

        # Group by DSP
        by_dsp: dict[str, list[UnhealthyDeal]] = {}
        for d in ssp_deals:
            by_dsp.setdefault(d.parsed.dsp or "Unknown", []).append(d)

        for dsp_name in sorted(by_dsp, key=lambda s: -sum(d.bid_requests for d in by_dsp[s])):
            dsp_deals = by_dsp[dsp_name]
            body_rows.append(_group_header(
                "DSP", dsp_name, len(dsp_deals),
                sum(d.bid_requests for d in dsp_deals),
                indent_px=36, bg=TINT_BLUE, text_color=TEXT_PRIMARY,
            ))

            # Group by (Advertiser, Agency)
            by_adv: dict[tuple[str, str], list[UnhealthyDeal]] = {}
            for d in dsp_deals:
                key = (d.parsed.advertiser or "Unattributed",
                       d.parsed.agency or d.parsed.agency_holdco or "")
                by_adv.setdefault(key, []).append(d)

            for (adv, agency) in sorted(by_adv, key=lambda k: -sum(d.bid_requests for d in by_adv[k])):
                adv_deals = by_adv[(adv, agency)]
                label = adv if not agency else f"{adv}  &middot;  {_esc(agency)}"
                body_rows.append(_group_header(
                    "", label, len(adv_deals),
                    sum(d.bid_requests for d in adv_deals),
                    indent_px=54, bg=SUBTLE_GRAY_BG, text_color=TEXT_FAINT,
                ))
                # Sort deals by bid_requests descending.
                for d in sorted(adv_deals, key=lambda x: -x.bid_requests):
                    body_rows.append(_format_deal_line(d))

    table = _table(
        "".join(body_rows),
        bg=CONTENT_BG,
        extra_style=f"font-family:{FONT_STACK};",
    )
    return _row(_cell(table, bg=CONTENT_BG, style="padding:0 0 12px 0;"))


def _section_sellers(p: Payload) -> str:
    """Group deals by seller, descending by deal count. Exclude House + Unknown."""
    by_seller: dict[str, list[UnhealthyDeal]] = {}
    for d in p.deals:
        if d.parsed.seller in ("House", "Unknown"):
            continue
        by_seller.setdefault(d.parsed.seller, []).append(d)

    ordered = sorted(by_seller.items(), key=lambda kv: -len(kv[1]))
    return "".join(_seller_section(seller, deals) for seller, deals in ordered)


# ── section: footer ────────────────────────────────────────────────────────

def _section_footer(p: Payload) -> str:
    body = (
        f'<div style="font-size:11px; color:{TEXT_HINT}; line-height:1.6;'
        f' text-align:center; padding:18px 28px 24px 28px;">'
        f'Generated by Newsweek yield-dashboard &middot; '
        f'<a href="{_esc(p.dashboard_url)}" style="color:{BORDER_BLUE_LNK}; text-decoration:none;">'
        f'Open the live dashboard</a></div>'
    )
    return _row(_cell(body, bg=CONTENT_BG, style="padding:0;"))


# ── top-level renderer ─────────────────────────────────────────────────────

def render_email(payload: Payload) -> str:
    """Pure function: payload → HTML string. Safe for Outlook desktop, web,
    and the macOS Outlook PWA."""
    head = (
        '<!DOCTYPE html><html><head>'
        '<meta http-equiv="Content-Type" content="text/html; charset=UTF-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1.0">'
        f'<title>Newsweek Weekly Deal Health &mdash; {payload.report_date.strftime("%B %d, %Y")}</title>'
        '</head>'
    )

    content_table = _table(
        _section_header(payload.report_date)
        + _section_kpis(payload)
        + _section_summary(payload)
        + _section_breakdowns(payload)
        + _section_cta(payload)
        + _section_methodology(payload)
        + _section_sellers(payload)
        + _section_footer(payload),
        bg=CONTENT_BG,
        width=str(EMAIL_MAX_WIDTH_PX),
        extra_style=f"border-radius:8px; overflow:hidden; max-width:{EMAIL_MAX_WIDTH_PX}px;",
    )

    page_table = _table(
        _row(_cell(content_table, bg=PAGE_BG, align="center", style="padding:24px 12px;")),
        bg=PAGE_BG,
    )

    body = (
        f'<body style="margin:0; padding:0; font-family:{FONT_STACK};"'
        f' bgcolor="{PAGE_BG}">{page_table}</body>'
    )
    return head + body + "</html>"
