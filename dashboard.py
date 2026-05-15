"""
Minimal Streamlit dashboard pointing at the local cache.

Run with:
    streamlit run dashboard.py

Loads only from the SQLite cache populated by refresh_cache.py — never hits
Magnite directly. That's the whole point: the dashboard stays snappy regardless
of Magnite's queue.
"""

from __future__ import annotations

import os
from datetime import date, timedelta

import pandas as pd
import sqlalchemy
import streamlit as st


def _engine() -> sqlalchemy.Engine:
    url = st.secrets.get("DATABASE_URL", os.environ.get("DATABASE_URL", ""))
    return sqlalchemy.create_engine(url)


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
    preset = st.selectbox("Date range", PRESETS, index=PRESETS.index("Yesterday"), key=f"{key}_preset")
    if preset == "Custom":
        dr = st.date_input("Custom range", value=(dmin, dmax), min_value=dmin, max_value=dmax, key=f"{key}_custom")
        start, end = dr if isinstance(dr, tuple) and len(dr) == 2 else (dmin, dmax)
    else:
        start, end = _preset_range(preset, dmin, dmax)
    return max(start, dmin), min(end, dmax)

DEAL_TYPE_NAMES = {
    "PA": "Private Auction",
    "PD": "Preferred Deal",
}

AE_NAMES = {
    "AShah": "Amit Shah",
    "BKaretny": "Ben Karetny",
    "BRobinson": "Brian Robinson",
    "DDivack": "Dana Divack",
    "DVarvaro": "Danielle Varvaro",
    "ILee": "Ivy Lee",
    "Ivy": "Ivy Lee",
    "JAmalfi": "Julie Amalfi",
    "JGentile": "Jeremy Gentile",
    "KWebb": "House",
    "RShore": "Rob Shore",
    "SCarroll": "Summer Carroll",
    "THern": "Theresa Hern",
    "House": "House",
}

st.set_page_config(page_title="Newsweek × Magnite", layout="wide")
st.title("Magnite DV+ — Performance")


@st.cache_data(ttl=300)
def load(table: str) -> pd.DataFrame:
    with _engine().connect() as conn:
        return pd.read_sql(f"SELECT * FROM {table}", conn)


tab_site, tab_dsp, tab_deal = st.tabs(["By Site / Size", "By DSP", "By Deal"])

with tab_site:
    df = load("by_site_size_daily")
    if df.empty:
        st.info("No data yet.")
    else:
        last_pull = df["_pulled_at"].max() if "_pulled_at" in df else "unknown"
        st.caption(f"Last refresh: {last_pull}")

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
    df = load("by_deal_daily")
    if df.empty:
        st.info("No data yet.")
    else:
        last_pull = df["_pulled_at"].max() if "_pulled_at" in df else "unknown"
        st.caption(f"Last refresh: {last_pull}")

        df = df.copy()
        df = df[
            df["deal"].notna()
            & (df["deal"].astype(str).str.strip(" -").str.upper() != "N/A")
            & df["deal_id"].notna()
            & (df["deal_id"].astype(str).str.strip() != "0")
        ]
        df["date"] = pd.to_datetime(df["date"]).dt.date
        df["seller_ae"] = df["deal"].str.extract(
            r"Team-(?:USA|INTL)_([A-Za-z]+)", expand=False
        )
        df["seller_ae"] = df["seller_ae"].map(AE_NAMES).fillna(df["seller_ae"])
        df["deal_type"] = df["deal"].str.extract(r"^Newsweek_(PA|PD)_", expand=False)
        df["deal_type"] = df["deal_type"].map(DEAL_TYPE_NAMES).fillna(df["deal_type"])
        dmin, dmax = df["date"].min(), df["date"].max()

        start, end = date_filter("deal", dmin, dmax)

        f1, f2, f3 = st.columns(3)
        with f1:
            types = st.multiselect(
                "Filter deal type",
                sorted(df["deal_type"].dropna().unique()),
                key="deal_type_filter",
            )
        with f2:
            aes = st.multiselect(
                "Filter seller (AE)",
                sorted(df["seller_ae"].dropna().unique()),
                key="deal_ae_filter",
            )
        with f3:
            deals = st.multiselect(
                "Filter deals",
                sorted(df["deal"].dropna().unique()),
                key="deal_filter",
            )

        deal_search = st.text_input("Search deals by name", placeholder="Type to filter…", key="deal_search")

        view = df[(df["date"] >= start) & (df["date"] <= end)]
        if types:
            view = view[view["deal_type"].isin(types)]
        if aes:
            view = view[view["seller_ae"].isin(aes)]
        if deals:
            view = view[view["deal"].isin(deals)]
        if deal_search:
            view = view[view["deal"].str.contains(deal_search, case=False, na=False)]

        c1, c2, c3 = st.columns(3)
        c1.metric("Impressions", f"{view['impressions'].sum():,}")
        c2.metric("Gross revenue", f"${view['publisher_gross_revenue'].sum():,.2f}")
        c3.metric("Net revenue", f"${view['seller_net_revenue'].sum():,.2f}")

        # Deals with no impressions
        if len(view) > 0:
            zero_imp = view.groupby("deal")["impressions"].sum()
            zero_imp = zero_imp[zero_imp == 0]
            if not zero_imp.empty:
                names = ", ".join(zero_imp.index.tolist()[:5])
                st.warning(f"{len(zero_imp)} deal(s) with 0 impressions in selected period: {names}")

        col_trend, col_ae = st.columns(2)
        with col_trend:
            st.subheader("Daily revenue")
            daily = view.groupby("date")["publisher_gross_revenue"].sum().rename("Revenue ($)")
            st.line_chart(daily, height=220)
        with col_ae:
            st.subheader("Revenue by AE")
            ae_rev = view.groupby("seller_ae")["publisher_gross_revenue"].sum().sort_values(ascending=False)
            st.bar_chart(ae_rev, height=220)

        st.dataframe(
            view.sort_values("publisher_gross_revenue", ascending=False),
            use_container_width=True,
            column_config={
                "_pulled_at": None,
                "seller_ae": None,
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
    df = load("by_dsp_daily")
    if df.empty:
        st.info("No data yet.")
    else:
        last_pull = df["_pulled_at"].max() if "_pulled_at" in df else "unknown"
        st.caption(f"Last refresh: {last_pull}")

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

        col_trend, col_win = st.columns(2)
        with col_trend:
            st.subheader("Revenue trend – top 5 DSPs")
            top5 = view.groupby("partner")["publisher_gross_revenue"].sum().nlargest(5).index
            trend = (view[view["partner"].isin(top5)]
                     .groupby(["date", "partner"])["publisher_gross_revenue"]
                     .sum().unstack(fill_value=0))
            st.line_chart(trend, height=220)
        with col_win:
            st.subheader("Win rate by DSP (%)")
            win_chart = win_by_dsp.sort_values(ascending=False)
            st.bar_chart(win_chart, height=220)

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
