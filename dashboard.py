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

import pandas as pd
import sqlalchemy
import streamlit as st


def _engine() -> sqlalchemy.Engine:
    url = st.secrets.get("DATABASE_URL", os.environ.get("DATABASE_URL", ""))
    return sqlalchemy.create_engine(url)

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

        date_range = st.date_input(
            "Date range",
            value=(dmin, dmax),
            min_value=dmin,
            max_value=dmax,
        )

        f1, f2, f3 = st.columns(3)
        with f1:
            sites = st.multiselect("Filter sites", sorted(df["site"].dropna().unique()))
        with f2:
            sizes = st.multiselect("Filter sizes", sorted(df["size"].dropna().unique()))
        with f3:
            devices = st.multiselect("Filter device types", sorted(df["device_type_name_v1"].dropna().unique()))

        view = df
        if isinstance(date_range, tuple) and len(date_range) == 2:
            start, end = date_range
            view = view[(view["date"] >= start) & (view["date"] <= end)]
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

        date_range = st.date_input(
            "Date range",
            value=(dmin, dmax),
            min_value=dmin,
            max_value=dmax,
            key="deal_date_range",
        )

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

        view = df
        if isinstance(date_range, tuple) and len(date_range) == 2:
            start, end = date_range
            view = view[(view["date"] >= start) & (view["date"] <= end)]
        if types:
            view = view[view["deal_type"].isin(types)]
        if aes:
            view = view[view["seller_ae"].isin(aes)]
        if deals:
            view = view[view["deal"].isin(deals)]

        c1, c2, c3 = st.columns(3)
        c1.metric("Impressions", f"{view['impressions'].sum():,}")
        c2.metric("Gross revenue", f"${view['publisher_gross_revenue'].sum():,.2f}")
        c3.metric("Net revenue", f"${view['seller_net_revenue'].sum():,.2f}")

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

        date_range = st.date_input(
            "Date range",
            value=(dmin, dmax),
            min_value=dmin,
            max_value=dmax,
            key="dsp_date_range",
        )

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

        view = df
        if isinstance(date_range, tuple) and len(date_range) == 2:
            start, end = date_range
            view = view[(view["date"] >= start) & (view["date"] <= end)]
        if partners:
            view = view[view["partner"].isin(partners)]
        if sites_dsp:
            view = view[view["site"].isin(sites_dsp)]

        c1, c2, c3 = st.columns(3)
        c1.metric("Impressions", f"{view['impressions'].sum():,}")
        c2.metric("Gross revenue", f"${view['publisher_gross_revenue'].sum():,.2f}")
        c3.metric("Auctions won", f"{view['auctions_won'].sum():,}")

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
