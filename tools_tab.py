"""The dashboard's Tools tab: name builder, final reports, screenshot decks.

These used to live outside the dashboard — the Taxonomy Standard workbook on
SharePoint, and GitHub workflows someone dispatched by hand (or asked Claude
to). Here they run from the Newsweek-login dashboard on Azure:

- **Name builder** is pure (``dashboard_logic.taxonomy_name``), no network.
- **Final report** and **Screenshots** start the same read-only GitHub
  workflows (``final_campaign_report.yml``, ``pull_screenshots_source.yml``,
  ``capture_screenshots.yml``) through ``gh_actions`` and hand back the files
  they make: the client workbook (.xlsx) and the deck (.pptx).

dashboard.py renders the tab by calling ``render(settings)``; decisions live
in dashboard_logic / the scripts, per the testability rule in CLAUDE.md.
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import streamlit as st

import dashboard_logic as dl
import gh_actions as gha

_ET = ZoneInfo("America/New_York")
FINAL_WF = "final_campaign_report.yml"
PLAN_WF = "pull_screenshots_source.yml"
CAPTURE_WF = "capture_screenshots.yml"

# Common values, offered first; every field still takes free text.
_VERTICALS = ["Auto", "Finance", "Health", "Retail", "Tech", "Travel", "Entertainment",
              "Gambling", "Education", "Politics", "CPG", "Multi-Category"]
_SSPS = ["NA", "Magnite", "PubMatic", "Index", "AdX", "Xandr", "OpenPath",
         "Meta", "TikTok", "LinkedIn", "X", "YouTube"]
_DSPS = ["NA", "DV360", "TTD", "Adobe", "Amazon", "Yahoo", "Xandr"]


@st.cache_data(ttl=20, show_spinner=False)
def _recent_runs(workflow: str) -> list[dict]:
    # st.tabs renders every tab on each rerun, so without this each keystroke in
    # the name builder would cost three GitHub calls.
    return gha.recent_runs(workflow, n=6)


def _when(iso: str) -> str:
    try:
        d = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(_ET)
        return d.strftime("%-d %b, %-I:%M %p ET")
    except ValueError:
        return iso


def _pick(label: str, options: list[str], key: str, default: str = "") -> str:
    """Selectbox of the usual values + 'Other…' free text."""
    opts = options + ["Other…"]
    idx = opts.index(default) if default in opts else 0
    choice = st.selectbox(label, opts, index=idx, key=key)
    if choice == "Other…":
        return st.text_input(f"{label} (type it)", key=f"{key}_other")
    return choice


# ── Name builder ─────────────────────────────────────────────────────────
def _name_builder(settings: dict) -> None:
    st.markdown("Builds a campaign or deal name to the **naming standard** (SOP 1.3): "
                "14 fields, in order, joined by underscores. The dashboard reads the "
                "advertiser, campaign and floor back out of these positions, so a name "
                "built anywhere else has to follow the same shape.")
    sellers = sorted({k for k, v in (settings.get("ae_names") or {}).items()
                      if v != "House" and k[:1].isupper() and k[1:2].isupper()})
    v: dict = {}
    c1, c2, c3 = st.columns(3)
    with c1:
        v["site"] = st.selectbox("1 · Site", list(dl.TAXONOMY_SITES), key="tx_site")
        v["deal_type"] = st.selectbox("2 · Deal type", list(dl.TAXONOMY_DEAL_TYPES), key="tx_dt",
                                      help="Direct · PD preferred deal · PA private auction · "
                                           "PG programmatic guaranteed · House")
        v["vertical"] = _pick("3 · Vertical (IAB)", _VERTICALS, "tx_vert")
        direct = v["deal_type"] in ("Direct", "House")
        v["ssp"] = _pick("4 · SSP", _SSPS, "tx_ssp", "NA" if direct else "Magnite")
        v["dsp"] = _pick("5 · DSP", _DSPS, "tx_dsp", "NA" if direct else "DV360")
    with c2:
        v["holding"] = st.text_input("6 · Holding company", placeholder="NA if none", key="tx_hold")
        v["agency"] = st.text_input("7 · Agency", placeholder="NA if client-direct", key="tx_ag")
        v["client"] = st.text_input("8 · Client / advertiser", placeholder="e.g. CITI", key="tx_cl")
        v["campaign"] = st.text_input("9 · Campaign", placeholder="e.g. FY24 or Holiday", key="tx_camp")
        v["geo"] = st.text_input("10 · Geo", value="US", key="tx_geo",
                                 help="US · up to three codes joined by hyphens (US-UK-CA) · "
                                      "a region for four or more (EMEA, APAC, LATAM, Global)")
    with c3:
        v["media_type"] = st.selectbox("11 · Media type", list(dl.TAXONOMY_MEDIA_TYPES), key="tx_mt")
        line_item = st.checkbox("This is a line item (IO#-row#)", key="tx_li",
                                disabled=not direct)
        v["cpm_or_io"] = st.text_input(
            "12 · " + ("NetSuite IO" if direct else "CPM floor"),
            placeholder=("SO00687-3" if line_item else "SO00687") if direct else "$16", key="tx_cpm")
        v["team"] = st.selectbox("13 · Team", list(dl.TAXONOMY_TEAMS), key="tx_team")
        v["seller"] = _pick("14 · Seller", sellers, "tx_seller")
    name, problems = dl.taxonomy_name(v, line_item=line_item and direct)
    st.markdown("**Name**")
    st.code(name, language=None)
    if problems:
        st.warning("Not to standard yet:\n\n" + "\n".join(f"- {p}" for p in problems))
    else:
        st.success("Follows the standard. Copy it with the button on the right of the box.")
    st.caption("Files: Newsweek_Advertiser_Client_Campaign_ReportType_Date · "
               "New GAM advertisers: [nw] Billing Entity (the billed party in NetSuite).")


# ── shared: run list with downloads ──────────────────────────────────────
def _runs(workflow: str, key: str, wanted: tuple[str, ...]) -> None:
    """Recent runs of ``workflow``; for a finished one, its files in ``wanted``
    (suffixes) become download buttons."""
    hdr, btn = st.columns([4, 1])
    hdr.markdown("**Recent runs**")
    if btn.button("Refresh", key=f"{key}_refresh"):
        st.session_state.pop(f"{key}_files", None)
        _recent_runs.clear()
    try:
        runs = _recent_runs(workflow)
    except Exception as e:  # noqa: BLE001
        st.error(f"Couldn't read the runs from GitHub: {e}")
        return
    if not runs:
        st.caption("No runs yet.")
        return
    files = st.session_state.setdefault(f"{key}_files", {})
    for r in runs:
        label = gha.run_label(r)
        dot = {"Done": "🟢", "Failed": "🔴", "Running": "🟡", "Queued": "⚪"}.get(label, "⚪")
        row = st.columns([5, 2, 3])
        row[0].markdown(f"{dot} {r['title']}  \n<span style='color:#57564f;font-size:12px'>"
                        f"{_when(r['created_at'])} · [log]({r['url']})</span>",
                        unsafe_allow_html=True)
        row[1].markdown(label)
        if label != "Done":
            continue
        got = files.get(r["id"])
        if got is None:
            if row[2].button("Get files", key=f"{key}_get_{r['id']}"):
                got = {}
                try:
                    for a in gha.run_artifacts(r["id"]):
                        if a["expired"]:
                            continue
                        for path, data in gha.artifact_files(a["id"]).items():
                            if path.endswith(wanted):
                                got[path.split("/")[-1]] = data
                except Exception as e:  # noqa: BLE001
                    st.error(f"Couldn't download: {e}")
                files[r["id"]] = got
        if got is not None:
            if not got:
                row[2].caption("No files (expired after 90 days, or none made).")
            for fname, data in got.items():
                row[2].download_button(f"⬇ {fname}", data, file_name=fname,
                                       key=f"{key}_dl_{r['id']}_{fname}")
                if fname.endswith(".md"):
                    with st.expander(f"Read {fname}", expanded=True):
                        st.markdown(data.decode("utf-8", "replace"))


def _dispatch(workflow: str, inputs: dict, what: str) -> None:
    try:
        gha.dispatch(workflow, inputs)
    except Exception as e:  # noqa: BLE001
        st.error(f"Couldn't start the {what}: {e}")
        return
    _recent_runs.clear()
    st.success(f"Started the {what}. It shows up under Recent runs in a few seconds "
               "(press Refresh); most finish in 2–5 minutes.")


# ── Final report ─────────────────────────────────────────────────────────
def _final_report() -> None:
    st.markdown("End-of-flight report for a Direct campaign: GAM delivery against goal, "
                "CTR, Active View and DoubleVerify attention for the whole flight, filled "
                "into the house workbook (the Sky Program layout). Advertiser, campaign, "
                "seller, flight and sizes come from the order name; override any of them "
                "below. Runbook: `docs/final_report.md`.")
    with st.form("fr_form"):
        c1, c2 = st.columns(2)
        order = c1.text_input("GAM order id", placeholder="e.g. 4194246183")
        end = c2.text_input("End date (optional)", placeholder="YYYY-MM-DD · blank = today")
        with st.expander("Workbook wording (optional — blank = from the order name)"):
            a1, a2 = st.columns(2)
            adv = a1.text_input("Advertiser")
            camp = a2.text_input("Campaign")
            seller = a1.text_input("Seller (full name)")
            flight = a2.text_input("Flight", placeholder="14 Sep 2026 – 26 Oct 2026")
            label = a1.text_input("Line label", placeholder="Display · 300x250, 970x250")
            ctr = a2.text_input("CTR target %", placeholder="0.10 display · 0.30 interstitial")
            omit = st.checkbox("Leave viewability and attention out (interstitials reading ~0%)")
            rev = st.checkbox("Add GAM revenue (leave off for $0-rate lines)")
        go = st.form_submit_button("Build final report", type="primary")
    if go:
        if not order.strip().isdigit():
            st.error("The GAM order id is a number, e.g. 4194246183.")
        else:
            _dispatch(FINAL_WF, {"order": order, "end_date": end, "advertiser": adv,
                                 "campaign": camp, "seller": seller, "flight": flight,
                                 "line_label": label, "ctr_target": ctr,
                                 "omit_measurement": omit, "revenue": rev}, "final report")
    st.caption("Check the workbook against GAM before it goes to the client. The client "
               "logo isn't added here — drop it in top right of the Summary page.")
    _runs(FINAL_WF, "fr", (".xlsx", ".md"))


# ── Screenshots ──────────────────────────────────────────────────────────
def _screenshots() -> None:
    st.markdown("Proof-of-placement deck for a Direct campaign. **Step 1** lists the "
                "line items, creatives and sizes to shoot. **Step 2** shoots every "
                "creative on one line item in context on a newsweek.com article "
                "(desktop + mobile, only where the size fits) and builds the client "
                "deck (.pptx). Runbook: `docs/screenshots_document.md`.")
    st.markdown("##### 1 · What to shoot")
    with st.form("ss_plan"):
        c1, c2 = st.columns(2)
        order = c1.text_input("GAM order id", key="ss_order")
        li = c2.text_input("…or a line item id", key="ss_li")
        go = st.form_submit_button("List line items and sizes")
    if go:
        if not (order.strip().isdigit() or li.strip().isdigit()):
            st.error("Give a GAM order id or a line item id (numbers).")
        else:
            _dispatch(PLAN_WF, {"order": order, "line_item": li}, "shot list")
    _runs(PLAN_WF, "ssp", (".md",))

    st.markdown("##### 2 · Shoot and build the deck")
    with st.form("ss_capture"):
        c1, c2 = st.columns([1, 2])
        li2 = c1.text_input("Line item id", key="ss_li2")
        url = c2.text_input("newsweek.com article", key="ss_url",
                            placeholder="https://www.newsweek.com/…")
        no_gate = st.checkbox("Shoot even if the page fails the vertical / brand-safety "
                              "check (a deliberate off-vertical shot)", key="ss_nogate")
        go2 = st.form_submit_button("Shoot and build deck", type="primary")
    if go2:
        if not li2.strip().isdigit():
            st.error("The line item id is a number.")
        elif "newsweek.com/" not in url:
            st.error("Pick a newsweek.com article in the client's industry.")
        else:
            _dispatch(CAPTURE_WF, {"line_item": li2, "article_url": url, "no_gate": no_gate},
                      "screenshot run")
    st.caption("The page has to be in the client's industry and brand safe — the run "
               "checks both and stops if it isn't. One deck per advertiser.")
    _runs(CAPTURE_WF, "ssc", (".pptx",))


def render(settings: dict) -> None:
    st.markdown('<div class="nw-section-eyebrow">Tools</div>', unsafe_allow_html=True)
    t1, t2, t3 = st.tabs(["Name builder", "Final report", "Screenshots"])
    with t1:
        _name_builder(settings)
    for tab, fn in ((t2, _final_report), (t3, _screenshots)):
        with tab:
            if not gha.configured():
                st.info("This needs the GitHub token (app setting GITHUB_DISPATCH_TOKEN, "
                        "from Key Vault). See docs/azure_dashboard.md.")
            else:
                fn()
