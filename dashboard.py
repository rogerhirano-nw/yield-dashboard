"""
Minimal Streamlit dashboard pointing at the local cache.

Run with:
    streamlit run dashboard.py

Loads only from the SQLite cache populated by refresh_cache.py — never hits
Magnite directly. That's the whole point: the dashboard stays snappy regardless
of Magnite's queue.
"""

from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

_ET = ZoneInfo("America/New_York")


def _fmt_last_refresh(ts: str) -> str:
    try:
        dt = datetime.fromisoformat(str(ts))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        dt_et = dt.astimezone(_ET)
        tz_label = "EDT" if dt_et.dst().seconds else "EST"
        return dt_et.strftime(f"%Y-%m-%d %I:%M %p {tz_label}")
    except Exception:
        return str(ts)

import altair as alt
import pandas as pd
import sqlalchemy
import streamlit as st

def _load_dotenv() -> None:
    env_file = Path(__file__).parent / ".env"
    if not env_file.exists():
        return
    for _line in env_file.read_text().splitlines():
        _line = _line.strip()
        if not _line or _line.startswith("#") or "=" not in _line:
            continue
        _k, _v = _line.split("=", 1)
        os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

_load_dotenv()


def _engine() -> sqlalchemy.Engine:
    try:
        url = st.secrets["DATABASE_URL"]
    except Exception:
        url = os.environ.get("DATABASE_URL", "")
    if not url:
        raise RuntimeError("DATABASE_URL is not set. Add it to .env or Streamlit secrets.")
    return sqlalchemy.create_engine(url)


# ── Settings ─────────────────────────────────────────────────────────────────

_SETTINGS_PATH = Path(__file__).parent / "settings.json"

_DEFAULT_SETTINGS: dict = {
    "ssps": [
        {
            "name": "GAM", "enabled": True, "table": "gam_campaigns",
            "deal_types": ["Private Auction", "Preferred Deal", "Programmatic Guaranteed"],
            "columns": {
                "Deal": "order_name", "Deal Type": "[auto]", "DSP": "",
                "Format": "[auto]", "Seller": "salesperson",
                "Paid Impressions": "lifetime_impressions_delivered",
                "Revenue": "ad_server_cpm_and_cpc_revenue", "eCPM": "cpm_rate",
                "Win Rate %": "", "Total Requests": "", "Bid Responses": "",
            },
        },
        {
            "name": "Magnite", "enabled": True, "table": "magnite_deal_daily",
            "deal_types": ["Private Auction", "Preferred Deal", "Private Marketplace"],
            "columns": {
                "Deal": "deal", "Deal Type": "[auto]", "DSP": "partner",
                "Format": "ad_format", "Seller": "[auto]",
                "Paid Impressions": "paid_impression",
                "Revenue": "publisher_gross_revenue", "eCPM": "ecpm",
                "Win Rate %": "[computed: impressions / requests]",
                "Total Requests": "bid_requests", "Bid Responses": "bid_responses",
            },
        },
        {
            "name": "Pubmatic", "enabled": True, "table": "pubmatic_deals",
            "deal_types": ["Private Auction", "Preferred Deal", "Programmatic Guaranteed", "Private Marketplace"],
            "columns": {
                "Deal": "deal", "Deal Type": "[auto]", "DSP": "dsp",
                "Format": "ad_format", "Seller": "[auto]",
                "Paid Impressions": "paid_impressions",
                "Revenue": "revenue", "eCPM": "ecpm",
                "Win Rate %": "win_rate", "Total Requests": "total_requests",
                "Bid Responses": "non_zero_bid_responses",
            },
        },
    ],
    "ae_names": {
        "AShah": "Amit Shah", "BKaretny": "Ben Karetny", "BRobinson": "Brian Robinson",
        "DDivack": "Dana Divack", "DVarvaro": "Danielle Varvaro",
        "ILee": "Ivy Lee", "Ivy": "Ivy Lee", "JAmalfi": "Julie Amalfi",
        "JGentile": "Jeremy Gentile", "KWebb": "House", "RShore": "Rob Shore",
        "SCarroll": "Summer Carroll", "THern": "Theresa Hern", "House": "House",
    },
    "deal_type_codes": {
        "PA": "Private Auction", "PD": "Preferred Deal",
        "PG": "Programmatic Guaranteed", "PMP": "Private Marketplace",
    },
    "direct_sources": [
        {
            "name": "GAM Direct",
            "enabled": True,
            "table": "gam_campaigns",
            "line_item_prefix": "Newsweek_Direct",
            "columns": {
                "Seller":        "seller_ae",
                "Advertiser":    "advertiser",
                "Campaign":      "campaign_name",
                "Line Item":     "line_item_name",
                "Format":        "ad_format",
                "Start Date":    "start_date",
                "End Date":      "end_date",
                "Goal":          "impressions_goal",
                "CPM Rate":      "cpm_rate",
                "Delivered":     "lifetime_impressions_delivered",
                "Impressions (1d)": "impressions_1d",
                "Remaining":     "remaining_impressions",
                "Clicks":        "ad_server_clicks",
                "Pacing %":      "pacing_pct",
                "Viewability %": "ad_server_active_view_viewable_impressions_rate",
                "CTR %":         "ad_server_ctr",
                "Revenue":       "ad_server_cpm_and_cpc_revenue",
                "VCR %":         "vcr",
            },
        },
    ],
}


def _load_settings() -> dict:
    # Primary: database (survives redeployments on Streamlit Cloud)
    try:
        with _engine().connect() as conn:
            row = conn.execute(
                sqlalchemy.text("SELECT value FROM dashboard_settings WHERE key = 'main'")
            ).fetchone()
            if row:
                return json.loads(row[0])
    except Exception:
        pass
    # Fallback: local file (useful for first-run and local dev)
    if _SETTINGS_PATH.exists():
        try:
            with open(_SETTINGS_PATH) as f:
                return json.load(f)
        except Exception:
            pass
    return _DEFAULT_SETTINGS


def _save_settings(data: dict) -> None:
    json_str = json.dumps(data, indent=2)
    now = datetime.now(timezone.utc).isoformat()
    # Write to database so changes survive redeployments
    with _engine().begin() as conn:
        conn.execute(sqlalchemy.text("""
            CREATE TABLE IF NOT EXISTS dashboard_settings (
                key        TEXT PRIMARY KEY,
                value      TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """))
        conn.execute(sqlalchemy.text(
            "DELETE FROM dashboard_settings WHERE key = 'main'"
        ))
        conn.execute(sqlalchemy.text(
            "INSERT INTO dashboard_settings (key, value, updated_at) VALUES ('main', :v, :t)"
        ), {"v": json_str, "t": now})
    # Also write locally so file stays in sync for dev
    try:
        with open(_SETTINGS_PATH, "w") as f:
            f.write(json_str)
    except Exception:
        pass
    st.cache_data.clear()


_cfg = _load_settings()
_ssp_enabled: dict[str, bool] = {s["name"]: s.get("enabled", True) for s in _cfg["ssps"]}


PRESETS = ["Year to date", "Month to date", "Last quarter", "Last 7 days", "Yesterday", "Custom"]


def _preset_range(preset: str, dmin: date, dmax: date) -> tuple[date, date]:
    today = date.today()
    if preset == "Yesterday":
        d = today - timedelta(days=1)
        return d, d
    if preset == "Last 7 days":
        return today - timedelta(days=7), today - timedelta(days=1)
    if preset == "Month to date":
        return today.replace(day=1), today - timedelta(days=1)
    if preset == "Last quarter":
        m = today.month
        y = today.year
        if m <= 3:
            return date(y - 1, 10, 1), date(y - 1, 12, 31)
        elif m <= 6:
            return date(y, 1, 1), date(y, 3, 31)
        elif m <= 9:
            return date(y, 4, 1), date(y, 6, 30)
        else:
            return date(y, 7, 1), date(y, 9, 30)
    if preset == "Year to date":
        return date(today.year, 1, 1), today - timedelta(days=1)
    return dmin, dmax  # Custom


def date_filter(key: str, dmin: date, dmax: date) -> tuple[date, date]:
    preset = st.selectbox("Date range", PRESETS, index=PRESETS.index("Last 7 days"), key=f"{key}_preset")
    if preset == "Custom":
        dr = st.date_input("Custom range", value=(dmin, dmax), min_value=dmin, max_value=dmax, key=f"{key}_custom")
        start, end = dr if isinstance(dr, tuple) and len(dr) == 2 else (dmin, dmax)
    else:
        start, end = _preset_range(preset, dmin, dmax)
    return max(start, dmin), min(end, dmax)

DEAL_TYPE_NAMES = _cfg["deal_type_codes"]

KNOWN_FORMATS = {"Display", "Native", "Video", "CTV", "OLV", "Banner"}


def _parse_deal(deal: str) -> pd.Series:
    """Extract fields from Newsweek structured deal name.

    Format: Newsweek_TYPE_VERTICAL_PLATFORM_DSP_..._FORMAT_$PRICE_Team-X_AE
    """
    empty = pd.Series({
        "revenue_source": "Open Market",
        "deal_type_label": None,
        "dsp": None,
        "ad_format": None,
        "floor_price": None,
    })
    raw = str(deal).strip() if deal else ""
    if not raw or raw.upper().replace("-", "").replace("/", "") in ("NA", "0"):
        return empty

    parts = raw.split("_")

    # Position 1 → deal type
    deal_type_label = None
    if len(parts) > 1:
        dt = parts[1].strip()
        deal_type_label = DEAL_TYPE_NAMES.get(dt, dt)

    # Position 3 → platform / revenue source
    revenue_source = "Publisher"
    if len(parts) > 3:
        platform = parts[3].strip().lower()
        if platform == "magnite":
            revenue_source = "Magnite"

    # Position 4 → DSP
    dsp = parts[4].strip() if len(parts) > 4 else None

    # Scan for format and floor price
    ad_format = floor_price = None
    for part in parts:
        p = part.strip()
        if p in KNOWN_FORMATS and ad_format is None:
            ad_format = p
        if p.startswith("$") and floor_price is None:
            floor_price = p

    return pd.Series({
        "revenue_source":  revenue_source,
        "deal_type_label": deal_type_label,
        "dsp":             dsp,
        "ad_format":       ad_format,
        "floor_price":     floor_price,
    })

AE_NAMES = _cfg["ae_names"]

st.set_page_config(page_title="Overall Performance", layout="wide")
st.title("Overall Performance")


@st.cache_data(ttl=300)
def load(table: str) -> pd.DataFrame:
    try:
        with _engine().connect() as conn:
            return pd.read_sql(f'SELECT * FROM "{table}"', conn)
    except Exception:
        return pd.DataFrame()


tab_seller, tab_site, tab_dsp, tab_deal, tab_pubmatic, tab_settings = st.tabs([
    "Campaigns", "By Site / Size", "By DSP", "Magnite Deals", "Pubmatic Deals", "⚙ Settings",
])

with tab_site:
    df = load("magnite_site_daily")
    if df.empty:
        st.info("No data yet.")
    else:
        last_pull = df["_pulled_at"].max() if "_pulled_at" in df else "unknown"
        st.caption(f"Last refresh: {_fmt_last_refresh(last_pull)}")

        df = df.copy()
        df["date"] = pd.to_datetime(df["date"]).dt.date
        dmin, dmax = df["date"].min(), df["date"].max()

        start, end = date_filter("site", dmin, dmax)

        f1, f2, f3 = st.columns(3)
        with f1:
            sites = st.multiselect("Filter sites", sorted(df["site"].dropna().unique()))
        with f2:
            sizes = st.multiselect("Filter sizes", sorted(df["size"].dropna().unique()))
        with f3:
            devices = st.multiselect("Filter device types", sorted(df["device_type_name_v1"].dropna().unique()))

        view = df[(df["date"] >= start) & (df["date"] <= end)]
        if sites:
            view = view[view["site"].isin(sites)]
        if sizes:
            view = view[view["size"].isin(sizes)]
        if devices:
            view = view[view["device_type_name_v1"].isin(devices)]

        c1, c2, c3 = st.columns(3)
        c1.metric("Ad requests", f"{view['ad_requests'].sum():,}")
        c2.metric("Impressions", f"{view['impressions'].sum():,}")
        c3.metric("Gross revenue", f"${view['publisher_gross_revenue'].sum():,.2f}")

        # WoW alert
        if len(view) > 0:
            max_d = view["date"].max()
            r7 = view[view["date"] > max_d - timedelta(days=7)]["publisher_gross_revenue"].sum()
            p7 = view[(view["date"] <= max_d - timedelta(days=7)) & (view["date"] > max_d - timedelta(days=14))]["publisher_gross_revenue"].sum()
            if p7 > 0:
                pct = (r7 - p7) / p7 * 100
                if pct <= -10:
                    st.warning(f"Revenue down {abs(pct):.1f}% vs prior week (${r7:,.0f} vs ${p7:,.0f})")
                elif pct >= 10:
                    st.success(f"Revenue up {pct:.1f}% vs prior week (${r7:,.0f} vs ${p7:,.0f})")

        col_trend, col_funnel = st.columns([2, 1])
        with col_trend:
            st.subheader("Daily revenue")
            daily = view.groupby("date")["publisher_gross_revenue"].sum().rename("Revenue ($)")
            st.line_chart(daily, height=220)
        with col_funnel:
            st.subheader("Bid funnel")
            funnel = pd.Series({
                "Ad requests": view["ad_requests"].sum(),
                "Bid requests": view["bid_requests"].sum(),
                "Impressions": view["impressions"].sum(),
            })
            st.bar_chart(funnel, height=220)

        st.dataframe(
            view,
            use_container_width=True,
            column_config={
                "_pulled_at": None,
                "ad_requests": st.column_config.NumberColumn(format="localized"),
                "bid_requests": st.column_config.NumberColumn(format="localized"),
                "bid_responses": st.column_config.NumberColumn(format="localized"),
                "auctions": st.column_config.NumberColumn(format="localized"),
                "impressions": st.column_config.NumberColumn(format="localized"),
                "publisher_gross_revenue": st.column_config.NumberColumn(format="dollar"),
                "ecpm": st.column_config.NumberColumn(format="dollar"),
            },
        )

with tab_deal:
    df = load("magnite_deal_daily")
    if df.empty:
        st.info("No data yet.")
    else:
        last_pull = df["_pulled_at"].max() if "_pulled_at" in df else "unknown"
        st.caption(f"Last refresh: {_fmt_last_refresh(last_pull)}")

        df = df.copy()
        df["date"] = pd.to_datetime(df["date"]).dt.date

        # Revenue source from deal_id (0 = Open Market, >0 = Deal)
        df["revenue_source"] = df["deal_id"].astype(str).str.strip().apply(
            lambda x: "Open Market" if x == "0" else "Deal"
        )
        # Normalize open market deal name
        open_market_mask = df["revenue_source"] == "Open Market"
        df.loc[open_market_mask, "deal"] = "Open Market"

        # Seller AE from deal name
        df["seller_ae"] = (
            df["deal"].str.extract(r"Team-(?:USA|INTL)_([A-Za-z]+)", expand=False)
            .map(AE_NAMES)
        )

        dmin, dmax = df["date"].min(), df["date"].max()
        start, end = date_filter("deal", dmin, dmax)

        f1, f2, f3, f4 = st.columns(4)
        with f1:
            rev_sources = st.multiselect(
                "Revenue source",
                sorted(df["revenue_source"].dropna().unique()),
                key="deal_rev_source_filter",
            )
        with f2:
            dsps = st.multiselect(
                "DSP",
                sorted(df["partner"].dropna().unique()) if "partner" in df.columns else [],
                key="deal_dsp_filter",
            )
        with f3:
            formats = st.multiselect(
                "Format",
                sorted(df["ad_format"].dropna().unique()) if "ad_format" in df.columns else [],
                key="deal_format_filter",
            )
        with f4:
            aes = st.multiselect(
                "Filter by Seller",
                sorted(df["seller_ae"].dropna().unique()),
                key="deal_ae_filter",
            )

        deal_search = st.text_input("Search deals by name", placeholder="Type to filter…", key="deal_search")

        view = df[(df["date"] >= start) & (df["date"] <= end)]
        if rev_sources:
            view = view[view["revenue_source"].isin(rev_sources)]
        if dsps:
            view = view[view["partner"].isin(dsps)]
        if formats:
            view = view[view["ad_format"].isin(formats)]
        if aes:
            view = view[view["seller_ae"].isin(aes)]
        if deal_search:
            view = view[view["deal"].str.contains(deal_search, case=False, na=False)]

        c1, c2, c3 = st.columns(3)
        c1.metric("Impressions", f"{view['impressions'].sum():,}")
        c2.metric("Gross revenue", f"${view['publisher_gross_revenue'].sum():,.2f}")
        c3.metric("Net revenue", f"${view['seller_net_revenue'].sum():,.2f}")

        # Zero-impression alert (exclude Open Market — it always has impressions)
        pmp_view = view[view["deal"] != "Open Market"]
        if len(pmp_view) > 0:
            zero_imp = pmp_view.groupby("deal")["impressions"].sum()
            zero_imp = zero_imp[zero_imp == 0]
            if not zero_imp.empty:
                st.warning(f"⚠️ {len(zero_imp)} deal(s) with 0 impressions — needs attention.")
                with st.expander("View deals"):
                    zero_df = zero_imp.reset_index()[["deal"]].rename(columns={"deal": "Deal"})
                    zero_df["Seller"] = (
                        zero_df["Deal"].str.extract(r"Team-(?:USA|INTL)_([A-Za-z]+)", expand=False)
                        .map(AE_NAMES).fillna("")
                    )
                    days_count = (
                        pmp_view[pmp_view["deal"].isin(zero_imp.index)]
                        .groupby("deal")["date"].nunique()
                    )
                    zero_df["Days with 0 impr."] = zero_df["Deal"].map(days_count).fillna(0).astype(int)
                    deal_metrics = (
                        pmp_view[pmp_view["deal"].isin(zero_imp.index)]
                        .groupby("deal")[["bid_requests", "bid_responses"]].sum()
                    )
                    zero_df["bid_requests"]  = zero_df["Deal"].map(deal_metrics["bid_requests"]).fillna(0)
                    zero_df["bid_responses"] = zero_df["Deal"].map(deal_metrics["bid_responses"]).fillna(0)

                    def _status(row):
                        if row["bid_requests"] == 0:
                            return "Deal not being sent to buyer — check trafficking"
                        if row["bid_responses"] == 0:
                            return "Buyer hasn't accepted the deal"
                        return "Accepted but not winning — check floor price or targeting"

                    zero_df["Status"] = zero_df.apply(_status, axis=1)
                    st.dataframe(
                        zero_df[["Deal", "Seller", "Days with 0 impr.", "Status"]]
                        .sort_values("Days with 0 impr.", ascending=False),
                        use_container_width=True,
                        hide_index=True,
                    )

        col_src, col_deals, col_ae = st.columns(3)
        with col_src:
            st.subheader("Revenue by source")
            src_rev = (
                view.groupby("revenue_source")["publisher_gross_revenue"]
                .sum().sort_values(ascending=True).rename("Revenue ($)")
            )
            st.bar_chart(src_rev, height=280, horizontal=True)
        pmp_view = view[view["revenue_source"] == "Deal"]
        with col_deals:
            st.subheader("Top 10 deals by revenue")
            top10_deals = (
                pmp_view.groupby("deal")["publisher_gross_revenue"]
                .sum().nlargest(10).reset_index()
                .rename(columns={"deal": "Deal", "publisher_gross_revenue": "Revenue"})
            )
            chart = alt.Chart(top10_deals).mark_bar().encode(
                x=alt.X("Revenue:Q", title="Revenue ($)"),
                y=alt.Y("Deal:N", sort="-x", title=None, axis=alt.Axis(labelLimit=500)),
                tooltip=["Deal", alt.Tooltip("Revenue:Q", format="$,.2f")],
            ).properties(height=320)
            st.altair_chart(chart, use_container_width=True)
        with col_ae:
            st.subheader("Revenue by Seller")
            ae_rev = (
                pmp_view.groupby("seller_ae")["publisher_gross_revenue"]
                .sum().sort_values(ascending=True).rename("Revenue ($)")
            )
            st.bar_chart(ae_rev, height=280, horizontal=True)

        st.dataframe(
            view.sort_values("publisher_gross_revenue", ascending=False),
            use_container_width=True,
            column_config={
                "_pulled_at": None,
                "seller_ae": None,
                "deal": st.column_config.TextColumn("Marketplace Deal Name"),
                "revenue_source": st.column_config.TextColumn("Revenue Source"),
                "partner": st.column_config.TextColumn("DSP"),
                "ad_format": st.column_config.TextColumn("Format"),
                "bid_requests": st.column_config.NumberColumn(format="localized"),
                "bid_responses": st.column_config.NumberColumn(format="localized"),
                "impressions": st.column_config.NumberColumn(format="localized"),
                "paid_impression": st.column_config.NumberColumn(format="localized"),
                "publisher_gross_revenue": st.column_config.NumberColumn(format="dollar"),
                "seller_net_revenue": st.column_config.NumberColumn(format="dollar"),
                "ecpm": st.column_config.NumberColumn(format="dollar"),
            },
        )

with tab_dsp:
    df = load("magnite_dsp_daily")
    if df.empty:
        st.info("No data yet.")
    else:
        last_pull = df["_pulled_at"].max() if "_pulled_at" in df else "unknown"
        st.caption(f"Last refresh: {_fmt_last_refresh(last_pull)}")

        df = df.copy()
        df["date"] = pd.to_datetime(df["date"]).dt.date
        dmin, dmax = df["date"].min(), df["date"].max()

        start, end = date_filter("dsp", dmin, dmax)

        f1, f2 = st.columns(2)
        with f1:
            partners = st.multiselect(
                "Filter DSPs",
                sorted(df["partner"].dropna().unique()),
                key="dsp_partner_filter",
            )
        with f2:
            sites_dsp = st.multiselect(
                "Filter sites",
                sorted(df["site"].dropna().unique()),
                key="dsp_site_filter",
            )

        view = df[(df["date"] >= start) & (df["date"] <= end)]
        if partners:
            view = view[view["partner"].isin(partners)]
        if sites_dsp:
            view = view[view["site"].isin(sites_dsp)]

        c1, c2, c3 = st.columns(3)
        c1.metric("Impressions", f"{view['impressions'].sum():,}")
        c2.metric("Gross revenue", f"${view['publisher_gross_revenue'].sum():,.2f}")
        c3.metric("Auctions won", f"{view['auctions_won'].sum():,}")

        # Low win rate alert
        if len(view) > 0:
            win_by_dsp = view.groupby("partner")["win_rate"].mean()
            low_win = win_by_dsp[win_by_dsp < 10].sort_values()
            if not low_win.empty:
                names = ", ".join([f"{p} ({v:.1f}%)" for p, v in low_win.items()])
                st.warning(f"Low win rate (<10%): {names}")

        col_top, col_issues = st.columns(2)
        with col_top:
            st.subheader("Top 10 DSPs by revenue")
            top10_rev = (view.groupby("partner")["publisher_gross_revenue"]
                         .sum().nlargest(10).sort_values(ascending=True)
                         .rename("Revenue ($)"))
            st.bar_chart(top10_rev, height=280, horizontal=True)
        with col_issues:
            st.subheader("DSPs to watch — low win rate")
            dsp_summary = (view.groupby("partner")
                           .agg(revenue=("publisher_gross_revenue", "sum"),
                                win_rate=("win_rate", "mean"))
                           .query("revenue > 0")
                           .sort_values("revenue", ascending=False)
                           .head(20))
            flagged = dsp_summary[dsp_summary["win_rate"] < 15].sort_values("revenue", ascending=False)
            if flagged.empty:
                st.success("No DSPs with revenue + low win rate issues.")
            else:
                st.dataframe(
                    flagged.reset_index().rename(columns={
                        "partner": "DSP",
                        "revenue": "Revenue ($)",
                        "win_rate": "Win Rate (%)",
                    }).style.format({"Revenue ($)": "${:,.2f}", "Win Rate (%)": "{:.1f}%"}),
                    use_container_width=True,
                    hide_index=True,
                )

        st.dataframe(
            view.sort_values("publisher_gross_revenue", ascending=False),
            use_container_width=True,
            column_config={
                "_pulled_at": None,
                "bid_requests": st.column_config.NumberColumn(format="localized"),
                "bid_responses": st.column_config.NumberColumn(format="localized"),
                "auctions_won": st.column_config.NumberColumn(format="localized"),
                "impressions": st.column_config.NumberColumn(format="localized"),
                "publisher_gross_revenue": st.column_config.NumberColumn(format="dollar"),
                "win_rate": st.column_config.NumberColumn(format="localized"),
            },
        )

with tab_pubmatic:
    try:
        pm_df = load("pubmatic_deals")
    except Exception:
        st.info("No Pubmatic data yet — run refresh_cache.py to populate pubmatic_deals.")
        pm_df = pd.DataFrame()

    if pm_df.empty:
        st.info("No Pubmatic data yet.")
    else:
        last_pull = pm_df["_pulled_at"].max() if "_pulled_at" in pm_df else "unknown"
        st.caption(f"Last refresh: {_fmt_last_refresh(last_pull)}")

        pm_df = pm_df.copy()
        pm_df["date"] = pd.to_datetime(pm_df["date"]).dt.date

        # Use publisher_deal_id as the display label when deal name is missing
        if "deal" not in pm_df.columns:
            pm_df["deal"] = None
        if "publisher_deal_id" not in pm_df.columns:
            pm_df["publisher_deal_id"] = None
        pm_df["deal_label"] = pm_df["deal"].fillna(pm_df["publisher_deal_id"]).fillna(pm_df["deal_meta_id"].astype(str))

        dmin, dmax = pm_df["date"].min(), pm_df["date"].max()
        start, end = date_filter("pubmatic", dmin, dmax)

        f1, f2, f3, f4 = st.columns(4)
        with f1:
            dsp_opts = sorted(pm_df["dsp"].dropna().unique()) if "dsp" in pm_df.columns else []
            sel_dsps = st.multiselect("DSP", dsp_opts, key="pm_dsp_filter")
        with f2:
            deal_type_opts = sorted(pm_df["deal"].dropna().str.extract(r"Newsweek_([^_]+)", expand=False).dropna().unique()) if "deal" in pm_df.columns else []
            sel_deal_types = st.multiselect("Deal type", deal_type_opts, key="pm_deal_type_filter")
        with f3:
            format_opts = sorted(pm_df["ad_format"].dropna().unique()) if "ad_format" in pm_df.columns else []
            sel_formats = st.multiselect("Format", format_opts, key="pm_format_filter")

        pm_search = st.text_input("Search deals by name", placeholder="Type to filter…", key="pm_deal_search")

        view = pm_df[(pm_df["date"] >= start) & (pm_df["date"] <= end)]
        if sel_dsps:
            view = view[view["dsp"].isin(sel_dsps)]
        if sel_deal_types:
            view = view[view["deal"].str.extract(r"Newsweek_([^_]+)", expand=False).isin(sel_deal_types)]
        if sel_formats and "ad_format" in view.columns:
            view = view[view["ad_format"].isin(sel_formats)]
        if pm_search:
            view = view[view["deal_label"].str.contains(pm_search, case=False, na=False)]

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Paid impressions", f"{view['paid_impressions'].sum():,.0f}")
        c2.metric("Revenue", f"${view['revenue'].sum():,.2f}")
        c3.metric("Avg eCPM", f"${view['ecpm'].mean():,.2f}" if len(view) else "—")
        c4.metric("Win rate", f"{view['win_rate'].mean():,.1f}%" if len(view) else "—")

        col_deals, col_dsps = st.columns(2)
        with col_deals:
            st.subheader("Top 10 deals by revenue")
            top_deals = (
                view.groupby("deal_label")["revenue"]
                .sum().nlargest(10).sort_values(ascending=True).reset_index()
                .rename(columns={"deal_label": "Deal", "revenue": "Revenue"})
            )
            if not top_deals.empty:
                chart = alt.Chart(top_deals).mark_bar().encode(
                    x=alt.X("Revenue:Q", title="Revenue ($)"),
                    y=alt.Y("Deal:N", sort="-x", title=None, axis=alt.Axis(labelLimit=400)),
                    tooltip=["Deal", alt.Tooltip("Revenue:Q", format="$,.2f")],
                ).properties(height=320)
                st.altair_chart(chart, use_container_width=True)

        with col_dsps:
            st.subheader("Top 10 DSPs by revenue")
            top_dsps = (
                view.groupby("dsp")["revenue"]
                .sum().nlargest(10).sort_values(ascending=True).reset_index()
                .rename(columns={"dsp": "DSP", "revenue": "Revenue"})
            ) if "dsp" in view.columns else pd.DataFrame()
            if not top_dsps.empty:
                chart_dsp = alt.Chart(top_dsps).mark_bar().encode(
                    x=alt.X("Revenue:Q", title="Revenue ($)"),
                    y=alt.Y("DSP:N", sort="-x", title=None, axis=alt.Axis(labelLimit=300)),
                    tooltip=["DSP", alt.Tooltip("Revenue:Q", format="$,.2f")],
                ).properties(height=320)
                st.altair_chart(chart_dsp, use_container_width=True)

        st.subheader("Daily revenue trend")
        daily_pm = view.groupby("date")["revenue"].sum().rename("Revenue ($)")
        st.line_chart(daily_pm, height=200)

        # Zero-response alert
        no_resp = (
            view.groupby("deal_label")
            .agg(paid_impressions=("paid_impressions", "sum"), responses=("non_zero_bid_responses", "sum"))
            .query("paid_impressions == 0 and responses == 0")
        ) if "non_zero_bid_responses" in view.columns else pd.DataFrame()
        if not no_resp.empty:
            st.warning(f"⚠️ {len(no_resp)} deal(s) with 0 paid impressions and 0 bid responses.")

        st.dataframe(
            view.sort_values("revenue", ascending=False),
            use_container_width=True,
            column_config={
                "_pulled_at": None,
                "source": None,
                "deal_meta_id": None,
                "dsp_id": None,
                "ad_format_id": None,
                "deal": st.column_config.TextColumn("Deal Name"),
                "deal_label": st.column_config.TextColumn("Deal"),
                "publisher_deal_id": st.column_config.TextColumn("Publisher Deal ID"),
                "dsp": st.column_config.TextColumn("DSP"),
                "paid_impressions": st.column_config.NumberColumn(format="localized"),
                "non_zero_bid_responses": st.column_config.NumberColumn("Bid Responses", format="localized"),
                "total_requests": st.column_config.NumberColumn(format="localized"),
                "revenue": st.column_config.NumberColumn(format="dollar"),
                "ecpm": st.column_config.NumberColumn(format="dollar"),
                "win_rate": st.column_config.NumberColumn("Win Rate %", format="localized"),
            },
        )

with tab_seller:
    # ── Table 1: Direct campaigns from GAM ──────────────────────────────
    st.subheader("Direct Campaigns")

    try:
        gam_df = load("gam_campaigns")
    except Exception:
        gam_df = pd.DataFrame()
        st.info("No GAM data yet. The gam_campaigns table will be created on the next scheduled refresh.")

    if gam_df.empty:
        st.info("No GAM data yet. Run refresh_cache.py to populate gam_campaigns.")
    else:
        last_pull = gam_df["_pulled_at"].max() if "_pulled_at" in gam_df else "unknown"
        st.caption(f"Last refresh: {_fmt_last_refresh(last_pull)}")

        gam_df = gam_df.copy()
        _direct_src = next((s for s in _cfg.get("direct_sources", []) if s.get("enabled", True)), None)
        _direct_prefix = _direct_src.get("line_item_prefix", "Newsweek_Direct") if _direct_src else "Newsweek_Direct"
        gam_df = gam_df[gam_df["line_item_name"].str.startswith(_direct_prefix, na=False)]
        gam_df = gam_df[~gam_df["order_name"].str.startswith("Newsweek_Test", na=False)]

        for datecol in ("start_date", "end_date"):
            if datecol in gam_df.columns:
                gam_df[datecol] = pd.to_datetime(gam_df[datecol], errors="coerce").dt.date

        for numcol in ("pacing_pct", "impressions_delivered", "impressions_1d", "lifetime_impressions_delivered", "impressions_goal", "cpm_rate",
                       "ad_server_cpm_and_cpc_revenue", "ad_server_ctr",
                       "ad_server_active_view_viewable_impressions_rate", "vcr",
                       "video_interaction_video_starts", "video_interaction_video_completions"):
            if numcol in gam_df.columns:
                gam_df[numcol] = pd.to_numeric(gam_df[numcol], errors="coerce")

        # Compute VCR from raw video columns (completions / starts × 100)
        if "video_interaction_video_starts" in gam_df.columns and \
                "video_interaction_video_completions" in gam_df.columns:
            _starts = gam_df["video_interaction_video_starts"]
            _completions = gam_df["video_interaction_video_completions"]
            gam_df["vcr"] = (_completions / _starts * 100).where(_starts > 0)

        # Extract seller — prefer GAM salesperson field, fall back to name regex
        if "salesperson" in gam_df.columns and gam_df["salesperson"].notna().any():
            gam_df["seller_ae"] = gam_df["salesperson"]
            _null_mask = gam_df["seller_ae"].isna()
            if _null_mask.any():
                _regex_seller = (
                    gam_df.loc[_null_mask, "order_name"]
                    .str.extract(r"Team-(?:USA|INTL)_([A-Za-z]+)", expand=False)
                    .map(AE_NAMES)
                )
                _li_seller = (
                    gam_df.loc[_null_mask, "line_item_name"]
                    .str.extract(r"Team-(?:USA|INTL)_([A-Za-z]+)", expand=False)
                    .map(AE_NAMES)
                )
                gam_df.loc[_null_mask, "seller_ae"] = _regex_seller.fillna(_li_seller)
        else:
            gam_df["seller_ae"] = (
                gam_df["order_name"]
                .str.extract(r"Team-(?:USA|INTL)_([A-Za-z]+)", expand=False)
                .map(AE_NAMES)
            )
            _li_seller = (
                gam_df["line_item_name"]
                .str.extract(r"Team-(?:USA|INTL)_([A-Za-z]+)", expand=False)
                .map(AE_NAMES)
            )
            gam_df["seller_ae"] = gam_df["seller_ae"].fillna(_li_seller)

        # Extract advertiser (index 7) and campaign (index 8) from line item name
        def _li_part(name, idx):
            if not isinstance(name, str):
                return None
            parts = name.split("_")
            return parts[idx].strip() if len(parts) > idx else None

        gam_df["advertiser"]    = gam_df["line_item_name"].apply(_li_part, idx=7)
        gam_df["campaign_name"] = gam_df["line_item_name"].apply(_li_part, idx=8)
        gam_df["ad_format"]     = gam_df["line_item_name"].apply(_li_part, idx=10)

        # Load Pubmatic sellers so they appear in the shared filter
        try:
            _pmp_sellers_df = load("pubmatic_deals")
            _pmp_sellers = (
                _pmp_sellers_df["deal"]
                .str.extract(r"Team-(?:USA|INTL)_([A-Za-z]+)", expand=False)
                .map(AE_NAMES)
                .dropna()
                .unique()
            ) if not _pmp_sellers_df.empty and "deal" in _pmp_sellers_df.columns else []
        except Exception:
            _pmp_sellers = []

        all_sellers = sorted(set(gam_df["seller_ae"].dropna().unique()) | set(_pmp_sellers))

        f1, f2, f3 = st.columns(3)
        with f1:
            selected_seller = st.selectbox(
                "Seller",
                options=["All"] + all_sellers,
                key="seller_select",
            )
        with f2:
            advertiser_opts = sorted(gam_df["advertiser"].dropna().unique())
            selected_advertisers = st.multiselect(
                "Advertiser",
                options=advertiser_opts,
                key="gam_advertiser_filter",
            )
        with f3:
            format_opts = sorted(gam_df["ad_format"].dropna().unique())
            selected_formats = st.multiselect(
                "Format",
                options=format_opts,
                key="gam_format_filter",
            )

        view_gam = gam_df if selected_seller == "All" else gam_df[gam_df["seller_ae"] == selected_seller].copy()
        if selected_advertisers:
            view_gam = view_gam[view_gam["advertiser"].isin(selected_advertisers)]
        if selected_formats:
            view_gam = view_gam[view_gam["ad_format"].isin(selected_formats)]

        if view_gam.empty:
            st.info("No campaigns found for the selected seller.")
        else:
            # ---------- Summary metrics ----------
            total_impr = view_gam["lifetime_impressions_delivered"].sum() if "lifetime_impressions_delivered" in view_gam else 0
            total_rev  = view_gam["ad_server_cpm_and_cpc_revenue"].sum() if "ad_server_cpm_and_cpc_revenue" in view_gam else 0
            avg_pacing = view_gam["pacing_pct"].mean() if "pacing_pct" in view_gam else None
            avg_viewability = (
                view_gam["ad_server_active_view_viewable_impressions_rate"].mean()
                if "ad_server_active_view_viewable_impressions_rate" in view_gam else None
            )
            avg_vcr = view_gam["vcr"].mean() if "vcr" in view_gam else None
            avg_ctr = view_gam["ad_server_ctr"].mean() if "ad_server_ctr" in view_gam else None

            m1, m2, m3, m4, m5, m6 = st.columns(6)
            m1.metric("Impressions", f"{int(total_impr):,}")
            m2.metric("Revenue", f"${total_rev:,.2f}")
            m3.metric("Avg Pacing %", f"{avg_pacing:.1f}%" if pd.notna(avg_pacing) else "—")
            m4.metric("Avg Viewability", f"{avg_viewability * 100:.1f}%" if pd.notna(avg_viewability) else "—")
            m5.metric("Avg VCR", f"{avg_vcr:.1f}%" if pd.notna(avg_vcr) else "—")
            m6.metric("Avg CTR", f"{avg_ctr * 100:.2f}%" if pd.notna(avg_ctr) else "—")

            # ---------- Pacing alerts ----------
            if "pacing_pct" in view_gam:
                under_pacing = view_gam[view_gam["pacing_pct"] < 85][
                    ["line_item_name", "order_name", "pacing_pct"]
                ].drop_duplicates("line_item_name")
                over_pacing = view_gam[view_gam["pacing_pct"] > 115][
                    ["line_item_name", "order_name", "pacing_pct"]
                ].drop_duplicates("line_item_name")

                if not under_pacing.empty:
                    names = ", ".join(
                        f"{r['line_item_name']} ({r['pacing_pct']:.0f}%)"
                        for _, r in under_pacing.iterrows()
                    )
                    st.warning(f"Under-pacing (<85%): {names}")
                if not over_pacing.empty:
                    names = ", ".join(
                        f"{r['line_item_name']} ({r['pacing_pct']:.0f}%)"
                        for _, r in over_pacing.iterrows()
                    )
                    st.success(f"Over-pacing (>115%): {names}")

            # ---------- Campaign table ----------
            # Remaining impressions (None when no goal is set)
            if "impressions_goal" in view_gam.columns and "lifetime_impressions_delivered" in view_gam.columns:
                view_gam = view_gam.copy()
                view_gam["remaining_impressions"] = view_gam.apply(
                    lambda r: max(r["impressions_goal"] - r["lifetime_impressions_delivered"], 0)
                    if pd.notna(r["impressions_goal"]) and pd.notna(r["lifetime_impressions_delivered"])
                    else None,
                    axis=1,
                )

            # CTR and viewability are stored as ratios (0–1); convert to percentage for display
            for _ratio_col in ("ad_server_ctr", "ad_server_active_view_viewable_impressions_rate"):
                if _ratio_col in view_gam.columns:
                    view_gam = view_gam.copy()
                    view_gam[_ratio_col] = view_gam[_ratio_col] * 100

            has_vcr = "vcr" in view_gam.columns and (
                view_gam["vcr"].notna().any()
                or ("ad_format" in view_gam.columns and view_gam["ad_format"].str.lower().eq("video").any())
            )

            _direct_col_map = _direct_src.get("columns", {}) if _direct_src else {}
            if _direct_col_map:
                # Build source_col → display_name from settings (preserving order)
                display_cols = {
                    src: name
                    for name, src in _direct_col_map.items()
                    if src and src not in ("N/A", "")
                }
            else:
                display_cols = {
                    "seller_ae": "Seller", "advertiser": "Advertiser",
                    "campaign_name": "Campaign", "line_item_name": "Line Item",
                    "ad_format": "Format", "start_date": "Start Date",
                    "end_date": "End Date", "impressions_goal": "Goal",
                    "cpm_rate": "CPM Rate",
                    "lifetime_impressions_delivered": "Delivered",
                    "impressions_1d": "Impressions (1d)",
                    "remaining_impressions": "Remaining",
                    "ad_server_clicks": "Clicks", "pacing_pct": "Pacing %",
                    "ad_server_active_view_viewable_impressions_rate": "Viewability %",
                    "ad_server_ctr": "CTR %",
                    "ad_server_cpm_and_cpc_revenue": "Revenue",
                }
            if has_vcr and "vcr" not in display_cols:
                display_cols["vcr"] = "VCR %"

            available_cols = [c for c in display_cols if c in view_gam.columns]
            table_df = (
                view_gam[available_cols]
                .drop_duplicates(subset=["line_item_name"] if "line_item_name" in available_cols else None)
                .rename(columns={c: display_cols[c] for c in available_cols})
                .sort_values("Pacing %" if "Pacing %" in [display_cols[c] for c in available_cols] else available_cols[0])
            )

            col_config = {}
            if "Goal" in table_df.columns:
                col_config["Goal"] = st.column_config.NumberColumn(format="localized")
            if "CPM Rate" in table_df.columns:
                col_config["CPM Rate"] = st.column_config.NumberColumn(format="dollar")
            if "Delivered" in table_df.columns:
                col_config["Delivered"] = st.column_config.NumberColumn(format="localized")
            if "Remaining" in table_df.columns:
                col_config["Remaining"] = st.column_config.NumberColumn(format="localized")
            if "Impressions (1d)" in table_df.columns:
                col_config["Impressions (1d)"] = st.column_config.NumberColumn(format="localized")
            if "Clicks" in table_df.columns:
                col_config["Clicks"] = st.column_config.NumberColumn(format="localized")
            if "Pacing %" in table_df.columns:
                col_config["Pacing %"] = st.column_config.NumberColumn(format="%.1f%%")
            if "Viewability %" in table_df.columns:
                col_config["Viewability %"] = st.column_config.NumberColumn(format="%.1f%%")
            if "VCR %" in table_df.columns:
                col_config["VCR %"] = st.column_config.NumberColumn(format="%.1f%%")
            if "CTR %" in table_df.columns:
                col_config["CTR %"] = st.column_config.NumberColumn(format="%.2f%%")
            if "Revenue" in table_df.columns:
                col_config["Revenue"] = st.column_config.NumberColumn(format="dollar")

            styled_df = table_df.style
            if "Viewability %" in table_df.columns:
                def _viewability_color(v):
                    if not isinstance(v, (int, float)) or pd.isna(v):
                        return ""
                    if v >= 70:
                        return "color: hsl(120, 60%, 35%)"
                    hue = int(max(0.0, v) / 70.0 * 120)
                    return f"color: hsl({hue}, 70%, 38%)"
                styled_df = styled_df.map(_viewability_color, subset=["Viewability %"])
            if "Pacing %" in table_df.columns:
                def _pacing_color(v):
                    if not isinstance(v, (int, float)) or pd.isna(v):
                        return ""
                    if v >= 100:
                        return "color: hsl(120, 60%, 35%)"
                    hue = int(max(0.0, v) / 100.0 * 120)
                    return f"color: hsl({hue}, 70%, 38%)"
                styled_df = styled_df.map(_pacing_color, subset=["Pacing %"])

            st.dataframe(
                styled_df,
                use_container_width=True,
                hide_index=True,
                column_config=col_config,
            )

    st.divider()

    # ── Table 2: PMP deals from Pubmatic ────────────────────────────────
    st.subheader("PMP Deals")

    # Build filter controls unconditionally — they must render even when Pubmatic is absent.
    _pmp_ssps_available = [s["name"] for s in _cfg["ssps"] if s.get("enabled", True)]
    _pmp_deal_types_available = sorted(set(
        dt for s in _cfg["ssps"] if s.get("enabled", True) for dt in s.get("deal_types", [])
    ))
    _pf1, _pf2, _pf3, _pf4 = st.columns(4)
    with _pf1:
        sel_pmp_deal_types = st.multiselect(
            "Deal Type",
            _pmp_deal_types_available,
            key="campaigns_pmp_deal_type_filter",
        )
    with _pf2:
        sel_pmp_ssps = st.multiselect(
            "SSP",
            _pmp_ssps_available,
            key="campaigns_pmp_ssp_filter",
        )
    with _pf3:
        sel_pmp_dsps = st.multiselect(
            "DSP",
            [],  # populated below after Magnite data is loaded
            key="campaigns_pmp_dsp_filter",
        )
    with _pf4:
        sel_pmp_formats = st.multiselect(
            "Format",
            [],  # populated below
            key="campaigns_pmp_format_filter",
        )

    # ── Pubmatic ──────────────────────────────────────────────────────────
    pmp_summary = pd.DataFrame()
    try:
        pmp_df = load("pubmatic_deals") if _ssp_enabled.get("Pubmatic", True) else pd.DataFrame()
    except Exception:
        pmp_df = pd.DataFrame()
    if pmp_df.empty:
        if _ssp_enabled.get("Pubmatic", True):
            st.info("No Pubmatic PMP data yet — run refresh_cache.py to populate pubmatic_deals.")
    else:
        pmp_df = pmp_df.copy()
        pmp_df["date"] = pd.to_datetime(pmp_df["date"]).dt.date
        if "deal" not in pmp_df.columns:
            pmp_df["deal"] = None
        if "publisher_deal_id" not in pmp_df.columns:
            pmp_df["publisher_deal_id"] = None
        pmp_df["deal_label"] = pmp_df["deal"].fillna(pmp_df["publisher_deal_id"]).fillna(pmp_df["deal_meta_id"].astype(str))
        pmp_df["seller_ae"] = (
            pmp_df["deal"].str.extract(r"Team-(?:USA|INTL)_([A-Za-z]+)", expand=False)
            .map(AE_NAMES)
        )
        if selected_seller != "All":
            pmp_df = pmp_df[pmp_df["seller_ae"] == selected_seller]
        pmp_df["deal_type_label"] = pmp_df["deal"].apply(lambda d: _parse_deal(d)["deal_type_label"])
        if sel_pmp_deal_types:
            pmp_df = pmp_df[pmp_df["deal_type_label"].isin(sel_pmp_deal_types)]
        pmp_df["ssp"] = "Pubmatic"
        pmp_summary = (
            pmp_df.groupby(["ssp", "deal_label", "deal_type_label", "ad_format", "dsp", "seller_ae"], dropna=False)
            .agg(
                paid_impressions=("paid_impressions", "sum"),
                revenue=("revenue", "sum"),
                ecpm=("ecpm", "mean"),
                win_rate=("win_rate", "mean"),
                total_requests=("total_requests", "sum"),
                non_zero_bid_responses=("non_zero_bid_responses", "sum"),
            )
            .reset_index()
            .rename(columns={
                "ssp": "SSP", "seller_ae": "Seller", "deal_label": "Deal",
                "deal_type_label": "Deal Type", "ad_format": "Format", "dsp": "DSP",
                "paid_impressions": "Paid Impressions", "revenue": "Revenue",
                "ecpm": "eCPM", "win_rate": "Win Rate %",
                "total_requests": "Total Requests", "non_zero_bid_responses": "Bid Responses",
            })
        )

    # Add GAM PA / PD / PG deals from the dedicated gam_pmp_deals table
    _gam_summary = pd.DataFrame()
    _gam_cfg_deal_types = next((s["deal_types"] for s in _cfg["ssps"] if s["name"] == "GAM"), [])
    _gam_deal_types = [t for t in (sel_pmp_deal_types or _gam_cfg_deal_types) if t in _gam_cfg_deal_types]
    if _gam_deal_types and _ssp_enabled.get("GAM", True):
        try:
            _gam_raw = load("gam_pmp_deals").copy()
            # Identify the deal name column (DEAL_NAME → deal_name after snake_case)
            _deal_col = next((c for c in _gam_raw.columns if "deal_name" in c or c == "deal"), None)
            if not _gam_raw.empty and _deal_col:
                _gam_raw = _gam_raw.rename(columns={_deal_col: "deal_name"})

                # Use programmatic_channel_name directly when available (REST API provides it).
                # Fall back to _parse_deal() for manually uploaded rows.
                _ch_col = next((c for c in _gam_raw.columns if "channel" in c.lower()), None)
                _channel_map = {
                    "Private Auction": "Private Auction",
                    "Preferred Deal": "Preferred Deal",
                    "Programmatic Guaranteed": "Programmatic Guaranteed",
                }
                if _ch_col:
                    _gam_raw["deal_type_label"] = (
                        _gam_raw[_ch_col]
                        .map(_channel_map)
                        .fillna(_gam_raw["deal_name"].apply(
                            lambda d: _parse_deal(str(d) if pd.notna(d) else "")["deal_type_label"]
                        ))
                    )
                else:
                    _gam_raw["deal_type_label"] = _gam_raw["deal_name"].apply(
                        lambda d: _parse_deal(str(d) if pd.notna(d) else "")["deal_type_label"]
                    )
                _gam_raw["ad_format"] = _gam_raw["deal_name"].apply(
                    lambda d: _parse_deal(str(d) if pd.notna(d) else "")["ad_format"]
                )
                _gam_raw["seller_ae"] = (
                    _gam_raw["deal_name"]
                    .str.extract(r"Team-(?:USA|INTL)_([A-Za-z]+)", expand=False)
                    .map(AE_NAMES)
                )
                _gam_deals = _gam_raw[_gam_raw["deal_type_label"].isin(_gam_deal_types)].copy()
                if selected_seller != "All":
                    _gam_deals = _gam_deals[_gam_deals["seller_ae"] == selected_seller]
                for _col in ("ad_server_impressions", "ad_server_cpm_and_cpc_revenue", "ad_server_average_ecpm"):
                    if _col in _gam_deals.columns:
                        _gam_deals[_col] = pd.to_numeric(_gam_deals[_col], errors="coerce")
                if sel_pmp_deal_types:
                    _gam_deals = _gam_deals[_gam_deals["deal_type_label"].isin(sel_pmp_deal_types)]
                if not _gam_deals.empty:
                    # Support both API column names and uploaded report column names
                    _rev_col  = next((c for c in ("revenue", "ad_server_cpm_and_cpc_revenue") if c in _gam_deals.columns), None)
                    _imp_col  = next((c for c in ("impressions", "ad_server_impressions") if c in _gam_deals.columns), None)
                    _ecpm_col = next((c for c in ("ecpm", "ad_server_average_ecpm") if c in _gam_deals.columns), None)
                    for _c in (_rev_col, _imp_col, _ecpm_col):
                        if _c:
                            _gam_deals[_c] = pd.to_numeric(_gam_deals[_c], errors="coerce")
                    _agg_kwargs = {}
                    if _imp_col:  _agg_kwargs["paid_impressions"] = (_imp_col, "sum")
                    if _rev_col:  _agg_kwargs["revenue"]          = (_rev_col, "sum")
                    if _ecpm_col: _agg_kwargs["ecpm"]             = (_ecpm_col, "mean")
                    _gam_agg = (
                        _gam_deals.groupby(["deal_name", "deal_type_label", "ad_format", "seller_ae"], dropna=False)
                        .agg(**_agg_kwargs)
                        .reset_index()
                    )
                    _gam_agg["SSP"] = "GAM"
                    _gam_agg["DSP"] = None
                    _gam_agg["Win Rate %"] = None
                    _gam_agg["Total Requests"] = None
                    _gam_agg["Bid Responses"] = None
                    _gam_summary = _gam_agg.rename(columns={
                        "seller_ae": "Seller",
                        "deal_name": "Deal",
                        "deal_type_label": "Deal Type",
                        "ad_format": "Format",
                        "paid_impressions": "Paid Impressions",
                        "revenue": "Revenue",
                        "ecpm": "eCPM",
                    })
        except Exception:
            pass

    # Add Magnite PA / PD / PMP deals (PG only comes from GAM)
    _magnite_summary = pd.DataFrame()
    _mag_cfg_deal_types = next((s["deal_types"] for s in _cfg["ssps"] if s["name"] == "Magnite"), [])
    _mag_types = [t for t in (sel_pmp_deal_types or _mag_cfg_deal_types) if t in _mag_cfg_deal_types]
    if _mag_types and _ssp_enabled.get("Magnite", True):
        try:
            _mag_df = load("magnite_deal_daily").copy()
            if not _mag_df.empty and "deal" in _mag_df.columns:
                _mag_df["deal_type_label"] = _mag_df["deal"].apply(lambda d: _parse_deal(d)["deal_type_label"])
                _mag_df = _mag_df[_mag_df["deal_type_label"].isin(_mag_types)]
                _mag_df["ssp"] = "Magnite"
                _mag_df["seller_ae"] = (
                    _mag_df["deal"].str.extract(r"Team-(?:USA|INTL)_([A-Za-z]+)", expand=False)
                    .map(AE_NAMES)
                )
                # Magnite deal API doesn't return partner/ad_format — derive from deal name.
                if "ad_format" not in _mag_df.columns:
                    _mag_df["ad_format"] = _mag_df["deal"].apply(lambda d: _parse_deal(d)["ad_format"])
                if "partner" not in _mag_df.columns:
                    _mag_df["partner"] = _mag_df["deal"].apply(lambda d: _parse_deal(d)["dsp"])
                if selected_seller != "All":
                    _mag_df = _mag_df[_mag_df["seller_ae"] == selected_seller]
                if not _mag_df.empty:
                    _mag_agg = (
                        _mag_df.groupby(["ssp", "deal", "deal_type_label", "ad_format", "partner", "seller_ae"], dropna=False)
                        .agg(
                            paid_impressions=("paid_impression", "sum"),
                            revenue=("publisher_gross_revenue", "sum"),
                            ecpm=("ecpm", "mean"),
                            total_requests=("bid_requests", "sum"),
                            non_zero_bid_responses=("bid_responses", "sum"),
                        )
                        .reset_index()
                    )
                    _mag_agg["Win Rate %"] = (
                        (_mag_agg["paid_impressions"] / _mag_agg["total_requests"] * 100)
                        .where(_mag_agg["total_requests"] > 0)
                    )
                    _magnite_summary = _mag_agg.rename(columns={
                        "ssp": "SSP",
                        "seller_ae": "Seller",
                        "deal": "Deal",
                        "deal_type_label": "Deal Type",
                        "ad_format": "Format",
                        "partner": "DSP",
                        "paid_impressions": "Paid Impressions",
                        "revenue": "Revenue",
                        "ecpm": "eCPM",
                        "total_requests": "Total Requests",
                        "non_zero_bid_responses": "Bid Responses",
                    })
        except Exception as _mag_exc:
            st.warning(f"Magnite PMP load error: {_mag_exc}")

    # Generic loader for any custom SSP added via the Settings tab
    _custom_summaries = []
    _builtin_ssps = {"GAM", "Magnite", "Pubmatic"}
    for _ssp_cfg in _cfg["ssps"]:
        _ssp_name = _ssp_cfg["name"]
        if _ssp_name in _builtin_ssps or not _ssp_cfg.get("enabled", True):
            continue
        try:
            _custom_df = load(_ssp_cfg["table"]).copy()
            if _custom_df.empty:
                continue
            _col_map = _ssp_cfg.get("columns", {})
            _rename = {}
            _field_to_internal = {
                "Deal": "Deal", "Deal Type": "Deal Type", "DSP": "DSP",
                "Format": "Format", "Seller": "Seller",
                "Paid Impressions": "paid_imp_raw", "Revenue": "rev_raw",
                "eCPM": "ecpm_raw", "Win Rate %": "wr_raw",
                "Total Requests": "tr_raw", "Bid Responses": "br_raw",
            }
            for field, src in _col_map.items():
                if src and src not in ("[auto]", "") and not src.startswith("[computed") and src in _custom_df.columns:
                    _rename[src] = _field_to_internal.get(field, field)
            _custom_df = _custom_df.rename(columns=_rename)
            _custom_df["SSP"] = _ssp_name
            if _col_map.get("Deal Type") == "[auto]" and "Deal" in _custom_df.columns:
                _custom_df["Deal Type"] = _custom_df["Deal"].apply(lambda d: _parse_deal(d)["deal_type_label"])
            if _col_map.get("Format") == "[auto]" and "Deal" in _custom_df.columns:
                _custom_df["Format"] = _custom_df["Deal"].apply(lambda d: _parse_deal(d)["ad_format"])
            if _col_map.get("Seller") == "[auto]" and "Deal" in _custom_df.columns:
                _custom_df["Seller"] = (
                    _custom_df["Deal"].str.extract(r"Team-(?:USA|INTL)_([A-Za-z]+)", expand=False).map(AE_NAMES)
                )
            _active_types = sel_pmp_deal_types or _ssp_cfg.get("deal_types", [])
            if _active_types and "Deal Type" in _custom_df.columns:
                _custom_df = _custom_df[_custom_df["Deal Type"].isin(_active_types)]
            if selected_seller != "All" and "Seller" in _custom_df.columns:
                _custom_df = _custom_df[_custom_df["Seller"] == selected_seller]
            if _custom_df.empty:
                continue
            _grp_cols = [c for c in ["SSP", "Deal", "Deal Type", "Format", "DSP", "Seller"] if c in _custom_df.columns]
            _agg_spec = {}
            for _metric, _raw, _how in [
                ("Paid Impressions", "paid_imp_raw", "sum"), ("Revenue", "rev_raw", "sum"),
                ("Total Requests", "tr_raw", "sum"), ("Bid Responses", "br_raw", "sum"),
                ("eCPM", "ecpm_raw", "mean"), ("Win Rate %", "wr_raw", "mean"),
            ]:
                if _raw in _custom_df.columns:
                    _custom_df[_raw] = pd.to_numeric(_custom_df[_raw], errors="coerce")
                    _agg_spec[_metric] = (_raw, _how)
            if _agg_spec:
                _custom_summaries.append(
                    _custom_df.groupby(_grp_cols, dropna=False).agg(**_agg_spec).reset_index()
                )
        except Exception:
            pass

    _parts = [df for df in [pmp_summary, _magnite_summary, _gam_summary] + _custom_summaries if not df.empty]
    if _parts:
        combined_pmp = pd.concat(_parts, ignore_index=True).sort_values("Revenue", ascending=False)
    else:
        combined_pmp = pd.DataFrame(columns=["SSP", "Deal", "Deal Type", "Format", "DSP", "Seller",
                                              "Paid Impressions", "Revenue", "eCPM",
                                              "Win Rate %", "Total Requests", "Bid Responses"])

    with st.expander("Debug: data source counts", expanded=False):
        st.write({
            "Pubmatic rows": len(pmp_summary),
            "Magnite rows": len(_magnite_summary),
            "GAM rows": len(_gam_summary),
            "Magnite deal types": _magnite_summary["Deal Type"].value_counts().to_dict() if not _magnite_summary.empty else {},
            "GAM deal types": _gam_summary["Deal Type"].value_counts().to_dict() if not _gam_summary.empty else {},
        })

    if sel_pmp_ssps:
        combined_pmp = combined_pmp[combined_pmp["SSP"].isin(sel_pmp_ssps)]
    if sel_pmp_dsps:
        combined_pmp = combined_pmp[combined_pmp["DSP"].isin(sel_pmp_dsps)]
    if sel_pmp_formats:
        combined_pmp = combined_pmp[combined_pmp["Format"].isin(sel_pmp_formats)]

    if combined_pmp.empty:
        st.info("No PMP deal data found. Check the Debug expander above for source counts, or run a data refresh.")
    else:
        pm1, pm2, pm3 = st.columns(3)
        pm1.metric("Paid impressions", f"{combined_pmp['Paid Impressions'].sum():,.0f}")
        pm2.metric("Revenue", f"${combined_pmp['Revenue'].sum():,.2f}")
        pm3.metric("Avg eCPM", f"${combined_pmp['eCPM'].mean():,.2f}" if len(combined_pmp) else "—")

        st.dataframe(
            combined_pmp,
            use_container_width=True,
            hide_index=True,
            column_config={
                "Paid Impressions": st.column_config.NumberColumn(format="localized"),
                "Revenue": st.column_config.NumberColumn(format="dollar"),
                "eCPM": st.column_config.NumberColumn(format="dollar"),
                "Win Rate %": st.column_config.NumberColumn(format="%.1f"),
                "Total Requests": st.column_config.NumberColumn(format="localized"),
                "Bid Responses": st.column_config.NumberColumn(format="localized"),
            },
        )

# ── Settings tab ─────────────────────────────────────────────────────────────

with tab_settings:
    st.subheader("Dashboard Settings")
    st.caption(
        "Configure data sources and column mappings for each SSP. "
        "Changes take effect immediately after saving. "
        "Add a new SSP by inserting a row in the Sources table and filling in its column mapping."
    )

    _s = _load_settings()  # fresh read so edits are based on current file

    _CANONICAL_FIELDS = [
        "Deal", "Deal Type", "DSP", "Format", "Seller",
        "Paid Impressions", "Revenue", "eCPM",
        "Win Rate %", "Total Requests", "Bid Responses",
    ]
    _ALL_DEAL_TYPES = [
        "Private Auction", "Preferred Deal",
        "Programmatic Guaranteed", "Private Marketplace",
    ]

    # ── Section 1: PMP Data Sources ─────────────────────────────────────
    st.markdown("#### PMP Data Sources")
    st.caption(
        "Each row is one SSP. **Deal Types** controls which deal types that SSP contributes to the PMP table. "
        "Disabling an SSP hides it from all filters and tables."
    )

    _ssp_rows = [
        {
            "SSP Name":       s["name"],
            "Enabled":        s.get("enabled", True),
            "Database Table": s["table"],
            "Deal Types":     ", ".join(s.get("deal_types", [])),
        }
        for s in _s["ssps"]
    ]
    _ssp_edit = st.data_editor(
        pd.DataFrame(_ssp_rows),
        use_container_width=True,
        hide_index=True,
        num_rows="dynamic",
        key="settings_ssps",
        column_config={
            "SSP Name":       st.column_config.TextColumn("SSP Name", required=True),
            "Enabled":        st.column_config.CheckboxColumn("Enabled"),
            "Database Table": st.column_config.TextColumn(
                "Database Table",
                help="SQLite/Postgres table populated by refresh_cache.py",
            ),
            "Deal Types": st.column_config.TextColumn(
                "Deal Types",
                help="Comma-separated list — e.g. Private Auction, Preferred Deal",
            ),
        },
    )

    # ── Section 2: Metrics and Dimensions Mapping ───────────────────────
    st.markdown("#### Metrics and Dimensions Mapping")
    st.caption(
        "Each row is a canonical display field; each column is an SSP. "
        "Use the dropdown in each cell to pick the matching source column from that SSP's table. "
        "**N/A** = not available. **[auto]** = computed from the deal name (type, format, seller)."
    )

    _edited_ssp_names = [
        str(row["SSP Name"]).strip()
        for _, row in _ssp_edit.iterrows()
        if pd.notna(row["SSP Name"]) and str(row["SSP Name"]).strip()
    ]
    _existing_col_maps = {s["name"]: s.get("columns", {}) for s in _s["ssps"]}
    _ssp_table_map = {
        str(row["SSP Name"]).strip(): str(row.get("Database Table", "")).strip()
        for _, row in _ssp_edit.iterrows()
        if pd.notna(row["SSP Name"]) and str(row["SSP Name"]).strip()
    }

    # Fetch the actual column names from each SSP's table so dropdowns show real options
    _SPECIAL_OPTS = ["N/A", "[auto]"]

    def _table_cols(table: str) -> list:
        try:
            with _engine().connect() as _c:
                _r = _c.execute(sqlalchemy.text(f'SELECT * FROM "{table}" LIMIT 0'))
                return [col for col in _r.keys() if not col.startswith("_")]
        except Exception:
            return []

    # Build options list per SSP: special values + real columns + any currently-stored custom values
    _ssp_opts: dict = {}
    for _sn in _edited_ssp_names:
        _tbl = _ssp_table_map.get(_sn, "")
        _real_cols = _table_cols(_tbl)
        _opts = _SPECIAL_OPTS + [c for c in _real_cols if c not in _SPECIAL_OPTS]
        # Ensure any currently-stored value is always a valid option
        for _f in _CANONICAL_FIELDS:
            _cur = _existing_col_maps.get(_sn, {}).get(_f, "")
            if _cur and _cur not in _opts:
                _opts.append(_cur)
        _ssp_opts[_sn] = _opts

    # Build matrix DataFrame
    _map_rows = []
    for _f in _CANONICAL_FIELDS:
        _row: dict = {"Field": _f}
        for _sn in _edited_ssp_names:
            _raw = _existing_col_maps.get(_sn, {}).get(_f, "") or ""
            _row[_sn] = _raw if _raw else "N/A"
        _map_rows.append(_row)
    _map_df = pd.DataFrame(_map_rows)

    _map_col_cfg: dict = {
        "Field": st.column_config.TextColumn("Field", disabled=True, width="small"),
    }
    for _sn in _edited_ssp_names:
        _tbl = _ssp_table_map.get(_sn, "")
        _map_col_cfg[_sn] = st.column_config.SelectboxColumn(
            _sn,
            options=_ssp_opts[_sn],
            width="medium",
            help=f"Source column from `{_tbl}`. N/A = not available. [auto] = parsed from deal name.",
            required=False,
        )

    _map_edit = st.data_editor(
        _map_df,
        use_container_width=True,
        hide_index=True,
        key="settings_colmap",
        column_config=_map_col_cfg,
        disabled=["Field"],
    )

    # ── Section 3: Direct Campaign Sources ──────────────────────────────
    st.markdown("#### Direct Campaign Sources")
    st.caption(
        "Each row is a direct-sold data source. **Line Item Prefix** filters the table to only direct campaigns. "
        "Disabling a source hides it from the Direct Campaigns table."
    )

    _direct_rows = [
        {
            "Source Name":      s["name"],
            "Enabled":          s.get("enabled", True),
            "Database Table":   s["table"],
            "Line Item Prefix": s.get("line_item_prefix", ""),
        }
        for s in _s.get("direct_sources", [])
    ]
    _direct_edit = st.data_editor(
        pd.DataFrame(_direct_rows) if _direct_rows else pd.DataFrame(
            columns=["Source Name", "Enabled", "Database Table", "Line Item Prefix"]
        ),
        use_container_width=True,
        hide_index=True,
        num_rows="dynamic",
        key="settings_direct_sources",
        column_config={
            "Source Name":      st.column_config.TextColumn("Source Name", required=True),
            "Enabled":          st.column_config.CheckboxColumn("Enabled"),
            "Database Table":   st.column_config.TextColumn(
                "Database Table",
                help="Table populated by refresh_cache.py (e.g. gam_campaigns)",
            ),
            "Line Item Prefix": st.column_config.TextColumn(
                "Line Item Prefix",
                help="Filter to line items whose name starts with this value (e.g. Newsweek_Direct)",
            ),
        },
    )

    st.markdown("##### Direct Campaign Metrics and Dimensions Mapping")
    st.caption(
        "Map each display field to its source column in the database table. "
        "Computed columns (seller_ae, advertiser, campaign_name, ad_format) are derived by the dashboard — "
        "select them as-is or map to a raw column."
    )

    _DIRECT_FIELDS = [
        "Seller", "Advertiser", "Campaign", "Line Item", "Format",
        "Start Date", "End Date", "Goal", "CPM Rate",
        "Delivered", "Impressions (1d)", "Remaining", "Clicks",
        "Pacing %", "Viewability %", "CTR %", "Revenue", "VCR %",
    ]
    _DIRECT_COMPUTED = ["seller_ae", "salesperson", "advertiser", "campaign_name", "ad_format", "remaining_impressions"]

    _existing_direct_maps = {s["name"]: s.get("columns", {}) for s in _s.get("direct_sources", [])}
    _direct_src_names = [
        str(r["Source Name"]).strip()
        for _, r in _direct_edit.iterrows()
        if pd.notna(r["Source Name"]) and str(r["Source Name"]).strip()
    ]
    _direct_table_map = {
        str(r["Source Name"]).strip(): str(r.get("Database Table", "")).strip()
        for _, r in _direct_edit.iterrows()
        if pd.notna(r["Source Name"]) and str(r["Source Name"]).strip()
    }

    _direct_ssp_opts: dict = {}
    for _dsn in _direct_src_names:
        _dtbl = _direct_table_map.get(_dsn, "")
        _dreal = _table_cols(_dtbl)
        _dopts = ["N/A"] + _DIRECT_COMPUTED + [c for c in _dreal if c not in (["N/A"] + _DIRECT_COMPUTED)]
        for _f in _DIRECT_FIELDS:
            _cur = _existing_direct_maps.get(_dsn, {}).get(_f, "")
            if _cur and _cur not in _dopts:
                _dopts.append(_cur)
        _direct_ssp_opts[_dsn] = _dopts

    _direct_map_rows = []
    for _f in _DIRECT_FIELDS:
        _row2: dict = {"Field": _f}
        for _dsn in _direct_src_names:
            _raw = _existing_direct_maps.get(_dsn, {}).get(_f, "") or ""
            _row2[_dsn] = _raw if _raw else "N/A"
        _direct_map_rows.append(_row2)

    _direct_map_col_cfg: dict = {
        "Field": st.column_config.TextColumn("Field", disabled=True, width="small"),
    }
    for _dsn in _direct_src_names:
        _dtbl = _direct_table_map.get(_dsn, "")
        _direct_map_col_cfg[_dsn] = st.column_config.SelectboxColumn(
            _dsn,
            options=_direct_ssp_opts[_dsn],
            width="medium",
            help=f"Source column from `{_dtbl}`. Computed columns: seller_ae, advertiser, campaign_name, ad_format.",
            required=False,
        )

    _direct_map_edit = st.data_editor(
        pd.DataFrame(_direct_map_rows) if _direct_map_rows else pd.DataFrame(columns=["Field"]),
        use_container_width=True,
        hide_index=True,
        key="settings_direct_colmap",
        column_config=_direct_map_col_cfg,
        disabled=["Field"],
    )

    # ── Section 4: Seller Mapping ────────────────────────────────────────
    st.markdown("#### Seller Mapping")
    st.caption("Maps short AE codes (from order and deal names) to full display names.")

    _ae_rows = [{"Code": k, "Full Name": v} for k, v in sorted(_s["ae_names"].items())]
    _ae_edit = st.data_editor(
        pd.DataFrame(_ae_rows) if _ae_rows else pd.DataFrame(columns=["Code", "Full Name"]),
        use_container_width=True,
        hide_index=True,
        num_rows="dynamic",
        key="settings_ae",
        column_config={
            "Code":      st.column_config.TextColumn("Code", required=True, help="e.g. JAmalfi"),
            "Full Name": st.column_config.TextColumn("Full Name", required=True, help="e.g. Julie Amalfi"),
        },
    )

    # ── Section 4: Deal Type Mapping ─────────────────────────────────────
    st.markdown("#### Deal Type Mapping")
    st.caption("Maps abbreviations in deal/order names to display labels.")

    _dt_rows = [{"Code": k, "Label": v} for k, v in sorted(_s["deal_type_codes"].items())]
    _dt_edit = st.data_editor(
        pd.DataFrame(_dt_rows) if _dt_rows else pd.DataFrame(columns=["Code", "Label"]),
        use_container_width=True,
        hide_index=True,
        num_rows="dynamic",
        key="settings_dt",
        column_config={
            "Code":  st.column_config.TextColumn("Code", required=True, help="e.g. PA, PD, PG, PMP"),
            "Label": st.column_config.TextColumn("Label", required=True, help="e.g. Private Auction"),
        },
    )

    # ── Section 5: GAM Deal Report Upload ────────────────────────────────
    st.divider()
    st.markdown("#### Upload GAM Deal Report")
    st.caption(
        "GAM's programmatic API doesn't expose deal-level breakdown for Private Auction. "
        "Export the report manually from GAM (Historical → Programmatic channel + Deal dimensions), "
        "then upload the Excel file here to populate the PMP Deals table."
    )
    _uploaded = st.file_uploader(
        "Upload GAM PMP report (.xlsx)",
        type=["xlsx"],
        key="gam_pmp_upload",
        help="GAM Historical report with Programmatic channel, Deal, Order dimensions",
    )
    if _uploaded is not None:
        try:
            import openpyxl as _openpyxl  # noqa: F401
            _xl = pd.read_excel(_uploaded, sheet_name=None)
            # Find the data sheet (not the Properties sheet)
            _data_sheet = next(
                (s for s in _xl if s.lower() not in ("properties", "cover")), None
            )
            if _data_sheet is None:
                st.error("Could not find a data sheet in the uploaded file.")
            else:
                _gam_upload_df = _xl[_data_sheet].copy()
                # Normalise column names (no _snake helper in dashboard — do it inline)
                import re as _re2
                def _norm_col(s):
                    s = _re2.sub(r"[^a-zA-Z0-9]+", "_", str(s)).strip("_").lower()
                    return s
                _gam_upload_df.columns = [_norm_col(c) for c in _gam_upload_df.columns]
                # Identify key columns by pattern
                _prog_col  = next((c for c in _gam_upload_df.columns if "programmatic" in c), None)
                _deal_col  = next((c for c in _gam_upload_df.columns if c == "deal" or c.endswith("_deal")), None)
                _order_col = next((c for c in _gam_upload_df.columns if "order" in c), None)
                _impr_col  = next((c for c in _gam_upload_df.columns if "impression" in c and "comparison" not in c and "change" not in c), None)
                _rev_col   = next((c for c in _gam_upload_df.columns if "revenue" in c and "comparison" not in c and "change" not in c), None)
                _ecpm_col  = next((c for c in _gam_upload_df.columns if ("ecpm" in c or "e_cpm" in c) and "comparison" not in c and "change" not in c), None)

                st.write(f"Detected columns — channel: `{_prog_col}`, deal: `{_deal_col}`, order: `{_order_col}`, impressions: `{_impr_col}`, revenue: `{_rev_col}`")

                if _deal_col:
                    _out = pd.DataFrame({
                        "date":            pd.Timestamp.now(tz="UTC").date().isoformat(),
                        "deal_name":       _gam_upload_df[_deal_col],
                        "programmatic_channel": _gam_upload_df[_prog_col] if _prog_col else None,
                        "order_name":      _gam_upload_df[_order_col] if _order_col else None,
                        "impressions":     pd.to_numeric(_gam_upload_df[_impr_col], errors="coerce") if _impr_col else None,
                        "revenue":         pd.to_numeric(_gam_upload_df[_rev_col], errors="coerce") if _rev_col else None,
                        "ecpm":            pd.to_numeric(_gam_upload_df[_ecpm_col], errors="coerce") if _ecpm_col else None,
                    })
                    _out = _out[_out["deal_name"].notna() & (_out["deal_name"].astype(str).str.startswith("Newsweek_"))]
                    _out["_pulled_at"] = datetime.now(timezone.utc).isoformat()
                    _out["source"] = "gam_upload"

                    st.dataframe(_out.head(20), use_container_width=True, hide_index=True)
                    st.write(f"**{len(_out)} deal rows detected**")

                    if st.button("📥  Import into gam_pmp_deals table", type="primary", key="gam_upload_confirm"):
                        try:
                            with _engine().begin() as _conn:
                                _conn.execute(sqlalchemy.text(
                                    'CREATE TABLE IF NOT EXISTS gam_pmp_deals '
                                    '(date TEXT, deal_name TEXT, programmatic_channel TEXT, '
                                    'order_name TEXT, impressions REAL, revenue REAL, ecpm REAL, '
                                    '_pulled_at TEXT, source TEXT)'
                                ))
                                _conn.execute(sqlalchemy.text(
                                    "DELETE FROM gam_pmp_deals WHERE source = 'gam_upload'"
                                ))
                            _out.to_sql("gam_pmp_deals", _engine(), if_exists="append", index=False)
                            st.success(f"Imported {len(_out)} rows into gam_pmp_deals. Reload the Campaigns tab to see PA deals.")
                            st.cache_data.clear()
                        except Exception as _ue:
                            st.error(f"Import failed: {_ue}")
                else:
                    st.warning("Could not identify the Deal column in the uploaded file.")
        except Exception as _ue:
            st.error(f"Failed to parse file: {_ue}")

    # ── Save ─────────────────────────────────────────────────────────────
    st.divider()
    if st.button("💾  Save Settings", type="primary"):
        try:
            _new_ssps = []
            for _, _row in _ssp_edit.iterrows():
                _ssp_name = str(_row.get("SSP Name", "")).strip()
                if not _ssp_name:
                    continue
                if _ssp_name in _map_edit.columns:
                    _col_map_new = {}
                    for _, _mr in _map_edit.iterrows():
                        _src = str(_mr[_ssp_name]).strip() if pd.notna(_mr[_ssp_name]) else "N/A"
                        _col_map_new[str(_mr["Field"])] = "" if _src == "N/A" else _src
                else:
                    _col_map_new = _existing_col_maps.get(_ssp_name, {})
                _dt_raw = str(_row.get("Deal Types", "")).strip()
                _new_ssps.append({
                    "name":       _ssp_name,
                    "enabled":    bool(_row.get("Enabled", True)),
                    "table":      str(_row.get("Database Table", "")).strip(),
                    "deal_types": [t.strip() for t in _dt_raw.split(",") if t.strip()],
                    "columns":    _col_map_new,
                })

            _new_ae = {
                str(r["Code"]).strip(): str(r["Full Name"]).strip()
                for _, r in _ae_edit.iterrows()
                if pd.notna(r.get("Code")) and str(r["Code"]).strip()
            }
            _new_dt = {
                str(r["Code"]).strip(): str(r["Label"]).strip()
                for _, r in _dt_edit.iterrows()
                if pd.notna(r.get("Code")) and str(r["Code"]).strip()
            }

            _new_direct = []
            for _, _row in _direct_edit.iterrows():
                _dsrc_name = str(_row.get("Source Name", "")).strip()
                if not _dsrc_name:
                    continue
                if _dsrc_name in _direct_map_edit.columns:
                    _dcol_map = {}
                    for _, _mr in _direct_map_edit.iterrows():
                        _src = str(_mr[_dsrc_name]).strip() if pd.notna(_mr[_dsrc_name]) else "N/A"
                        _dcol_map[str(_mr["Field"])] = "" if _src == "N/A" else _src
                else:
                    _dcol_map = _existing_direct_maps.get(_dsrc_name, {})
                _new_direct.append({
                    "name":             _dsrc_name,
                    "enabled":          bool(_row.get("Enabled", True)),
                    "table":            str(_row.get("Database Table", "")).strip(),
                    "line_item_prefix": str(_row.get("Line Item Prefix", "")).strip(),
                    "columns":          _dcol_map,
                })

            _save_settings({
                "ssps": _new_ssps, "ae_names": _new_ae,
                "deal_type_codes": _new_dt, "direct_sources": _new_direct,
            })
            st.success("Settings saved — reloading dashboard…")
            st.rerun()
        except Exception as _e:
            st.error(f"Failed to save: {_e}")
