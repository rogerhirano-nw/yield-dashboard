# newsweek_components.py
# Drop-in Streamlit render helpers for the Newsweek dashboard rebrand.
# These emit the EXACT on-brand HTML that pairs with newsweek-dashboard.css.
#
# WHY THIS FILE EXISTS: prose specs kept getting re-interpreted into
# st.metric / st.container(border=True), which regressed the design (tall empty
# rounded boxes, no lead metric, no top-rule). Use these functions verbatim and
# the markup matches the approved reference 1:1. Do not wrap their output in
# st.container(border=True) or st.metric — that is the regression.

import streamlit as st
from pathlib import Path


# ----------------------------------------------------------------------------
# 0) ONE-TIME SETUP — call once at the top of the app, before anything renders.
# ----------------------------------------------------------------------------
def inject_brand():
    """Load fonts + the Newsweek dashboard CSS into the Streamlit page.
    Call FIRST, once, e.g. right after st.set_page_config().

    Loads, in order:
      1. newsweek-fonts-embedded.css — base64-embedded @font-face. This is why
         the fonts now work: NO relative paths, NO static-file serving needed.
         The design system's own fonts.css uses url("../assets/...") paths that
         do NOT resolve inside a deployed Streamlit app, which is why the page
         was falling back to Georgia/system sans. Embedded data-URIs fix that.
      2. newsweek-dashboard.css — tokens + component styles.
    """
    here = Path(__file__).parent
    fonts = (here / "newsweek-fonts-embedded.css").read_text()
    css = (here / "newsweek-dashboard.css").read_text()
    st.markdown(f"<style>{fonts}</style>", unsafe_allow_html=True)
    st.markdown(f"<style>{css}</style>", unsafe_allow_html=True)
    # Make Streamlit's own chrome use the brand sans too.
    st.markdown(
        '<style>html,body,[class*="css"]{font-family:"Franklin Gothic",'
        'Arial,sans-serif;}</style>',
        unsafe_allow_html=True,
    )


def _spark(points, neutral=True):
    """Tiny inline sparkline. Stroke is NEUTRAL gray, never red (brand rule)."""
    if not points:
        return ""
    lo, hi = min(points), max(points)
    span = (hi - lo) or 1
    n = len(points)
    pts = " ".join(
        f"{(i/(n-1))*100:.1f},{22 - ((v-lo)/span)*20:.1f}" for i, v in enumerate(points)
    )
    return f'<svg class="nw-tile__spark" viewBox="0 0 100 22" preserveAspectRatio="none"><polyline points="{pts}"/></svg>'


# ----------------------------------------------------------------------------
# 1) KPI STRIP  — the part that keeps regressing. Render as ONE html block.
#    Pass the lead metric first; it gets the brand-red top rule + 30px figure.
#    EVERY tile must have a `tgt` sub-line so the row reads even.
# ----------------------------------------------------------------------------
def kpi_strip(tiles):
    """tiles: list of dicts -> {label, value, tgt, delta(optional float), spark(optional list), lead(optional bool)}
    Example:
        kpi_strip([
            {"label":"Revenue","value":"$63.9K","tgt":"▲ 14.3% vs 7-day avg","delta":14.3,"lead":True},
            {"label":"Paid Impressions","value":"34.31M","tgt":"vs 30.7M last week","delta":11.9},
            {"label":"Avg eCPM","value":"$1.86","tgt":"target $1.50","delta":4.0},
            {"label":"Active Deals","value":"312","tgt":"PD 41 · PA 188 · PMP 83"},
        ])
    """
    cells = []
    for t in tiles:
        lead = " nw-tile--lead" if t.get("lead") else ""
        delta = ""
        if t.get("delta") is not None:
            d = t["delta"]
            cls = "up" if d >= 0 else "dn"
            arrow = "▲" if d >= 0 else "▼"
            delta = f'<span class="nw-tile__delta {cls}">{arrow} {abs(d)}%</span>'
        spark = _spark(t["spark"]) if t.get("spark") else ""
        cells.append(
            f'<div class="nw-tile{lead}">{delta}'
            f'<div class="nw-tile__lab">{t["label"]}</div>'
            f'<div class="nw-tile__num">{t["value"]}</div>'
            f'<div class="nw-tile__tgt">{t.get("tgt","&nbsp;")}</div>'
            f"{spark}</div>"
        )
    # the grid auto-sizes to the number of tiles; override repeat(9) for 4-up etc.
    cols = len(tiles)
    st.markdown(
        f'<div class="nw-kpis" style="grid-template-columns:repeat({cols},1fr)">'
        + "".join(cells)
        + "</div>",
        unsafe_allow_html=True,
    )


# ----------------------------------------------------------------------------
# 2) TRIAGE FILTER STRIP  — replaces the empty 4-col "Needs you today" band.
#    Returns the clicked filter key via st.session_state so you can scope the
#    table below. Render as buttons so Streamlit can capture the click.
# ----------------------------------------------------------------------------
def triage_strip(filters, state_key="triage_filter"):
    """filters: list of {key, label, count, sev in ('all','crit','warn','info')}.
    Renders pills; sets st.session_state[state_key] to the active key.
    NOTE: Streamlit can't capture clicks inside st.markdown HTML, so this uses
    st.columns + st.button styled to look like .nw-fpill. Apply the .nw-fpill
    look via the CSS targeting [data-testid=stButton] inside .nw-triage-host, or
    use streamlit-extras / a components.html click bridge. Simplest robust path:
    one st.button per pill, label = f'{label}  {count}'."""
    if state_key not in st.session_state:
        st.session_state[state_key] = filters[0]["key"]
    st.markdown('<div class="nw-triage"><span class="nw-triage__lab">Show</span></div>',
                unsafe_allow_html=True)
    cols = st.columns(len(filters))
    for c, f in zip(cols, filters):
        active = st.session_state[state_key] == f["key"]
        if c.button(f'{f["label"]}  ·  {f["count"]}',
                    key=f'triage_{f["key"]}',
                    type="primary" if active else "secondary",
                    use_container_width=True):
            st.session_state[state_key] = f["key"]
            st.rerun()
    return st.session_state[state_key]


# ----------------------------------------------------------------------------
# 3) PRIORITY-FLIGHT MONITOR ROW  — compact; full detail behind a link/expander.
# ----------------------------------------------------------------------------
def flight_row(name, date_range, cpa, goal, over, stats, spark_points):
    """One slim CPA-monitor row. over=True -> red (missing goal), else green.
    stats: list of (label, value) up to 4. spark_points: daily CPA list."""
    sev = "crit" if over else "pos"
    pill_cls = "crit" if over else "pos"
    diff = abs(cpa - goal)
    pill_txt = (f"✗ ${diff:,.2f} over" if over else f"✓ ${diff:,.2f} under")
    stat_html = "".join(
        f'<div><div class="nw-tile__lab">{l}</div><div class="nw-tile__num" style="font-size:13px">{v}</div></div>'
        for l, v in stats[:4]
    )
    # area shaded above the goal line lives in the reference; this row shows the line only
    sp = _spark(spark_points)
    st.markdown(
        f'<div class="nw-flight nw-flight--{sev}">'
        f'<div><div class="nw-flight__nm">{name}</div><div class="nw-flight__dr">{date_range}</div></div>'
        f'<div class="nw-flight__cpa">${cpa:,.2f}</div>'
        f'<div><span class="nw-flight__goal nw-flight__goal--{pill_cls}">{pill_txt}</span></div>'
        f'<div style="display:grid;grid-template-columns:repeat({min(len(stats),4)},1fr);gap:10px">{stat_html}</div>'
        f'<a class="nw-flight__detail" href="#">View detail →</a>'
        f"</div>",
        unsafe_allow_html=True,
    )


# ----------------------------------------------------------------------------
# 4) BENCHMARK-BANDED TABLE CELL  — eCPM vs floor, pace vs target, etc.
# ----------------------------------------------------------------------------
def band(value, level):
    """level in ('good','warn','bad'). Returns an inline banded span (string).
    Use inside your own row markup. good=positive green, warn=amber, bad=critical red."""
    return f'<span class="nw-band nw-band--{level}">{value}</span>'


# ----------------------------------------------------------------------------
# 5) SECTION HEADER  — the Newsweek title treatment: red kicker eyebrow over a
#    serif headline. Use at the top of EVERY view/section so none render
#    title-less. Pairs with .nw-secthead in newsweek-dashboard.css.
# ----------------------------------------------------------------------------
def section_header(title, kicker=None, meta=None):
    """title: serif H1 (e.g. 'Overall performance'). kicker: small red eyebrow
    above it (e.g. 'Yield & Pacing'). meta: muted right-aligned note (e.g.
    '5:06 PM EDT · 39 line items')."""
    k = f'<div class="nw-secthead__kicker">{kicker}</div>' if kicker else ""
    m = f'<div class="nw-secthead__meta">{meta}</div>' if meta else ""
    st.markdown(
        f'<div class="nw-secthead">'
        f'<div>{k}<h1 class="nw-secthead__title">{title}</h1></div>{m}'
        f"</div>",
        unsafe_allow_html=True,
    )


# Smaller sub-section label (uppercase, red square accent) for blocks WITHIN a
# view — e.g. "PRIORITY FLIGHTS", "TODAY'S TOTALS", "DIRECT CAMPAIGNS".
def block_label(text, count=None):
    c = f' <span class="nw-blocklabel__ct">{count}</span>' if count is not None else ""
    st.markdown(f'<div class="nw-blocklabel">{text}{c}</div>', unsafe_allow_html=True)
