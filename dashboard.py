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

import altair as alt
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
    preset = st.selectbox("Date range", PRESETS, index=PRESETS.index("Last 7 days"), key=f"{key}_preset")
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


tab_site, tab_dsp, tab_deal, tab_seller = st.tabs(["By Site / Size", "By DSP", "By Deal", "Seller View"])

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
                "Filter by Seller",
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
                st.warning(f"⚠️ {len(zero_imp)} deal(s) with 0 impressions — needs attention.")
                with st.expander("View deals"):
                    zero_df = (zero_imp.reset_index()[["deal"]]
                               .rename(columns={"deal": "Deal"}))
                    zero_df["Seller"] = zero_df["Deal"].str.extract(r"Team-(?:USA|INTL)_([A-Za-z]+)", expand=False).map(AE_NAMES).fillna("")
                    days_count = (view[view["deal"].isin(zero_imp.index)]
                                  .groupby("deal")["date"].nunique())
                    zero_df["Days with 0 impr."] = zero_df["Deal"].map(days_count).fillna(0).astype(int)
                    deal_metrics = (view[view["deal"].isin(zero_imp.index)]
                                    .groupby("deal")[["bid_requests", "bid_responses"]].sum())
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

        col_deals, col_ae = st.columns(2)
        with col_deals:
            st.subheader("Top 10 deals by revenue")
            top10_deals = (view.groupby("deal")["publisher_gross_revenue"]
                           .sum().nlargest(10)
                           .reset_index()
                           .rename(columns={"deal": "Deal", "publisher_gross_revenue": "Revenue"}))
            chart = alt.Chart(top10_deals).mark_bar().encode(
                x=alt.X("Revenue:Q", title="Revenue ($)"),
                y=alt.Y("Deal:N", sort="-x", title=None, axis=alt.Axis(labelLimit=500)),
                tooltip=["Deal", alt.Tooltip("Revenue:Q", format="$,.2f")],
            ).properties(height=320)
            st.altair_chart(chart, use_container_width=True)
        with col_ae:
            st.subheader("Revenue by Seller")
            ae_rev = (view.groupby("seller_ae")["publisher_gross_revenue"]
                      .sum().sort_values(ascending=True).rename("Revenue ($)"))
            st.bar_chart(ae_rev, height=280, horizontal=True)

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

with tab_seller:
    import re as _re

    try:
        gam_df = load("campaigns_gam")
    except Exception:
        st.info("No GAM data yet. The campaigns_gam table will be created on the next scheduled refresh.")
        st.stop()

    if gam_df.empty:
        st.info("No GAM data yet. Run refresh_cache.py to populate campaigns_gam.")
    else:
        last_pull = gam_df["_pulled_at"].max() if "_pulled_at" in gam_df else "unknown"
        st.caption(f"Last refresh: {last_pull}")

        gam_df = gam_df.copy()

        # Parse dates from report_start / report_end; fall back to start_date/end_date
        for datecol in ("start_date", "end_date", "report_start", "report_end"):
            if datecol in gam_df.columns:
                gam_df[datecol] = pd.to_datetime(gam_df[datecol], errors="coerce").dt.date

        # Build a display date column for the date_filter helper
        if "start_date" in gam_df.columns:
            gam_df["_display_date"] = gam_df["start_date"]
        else:
            gam_df["_display_date"] = date.today()

        dmin_gam = gam_df["_display_date"].min() or date.today() - timedelta(days=7)
        dmax_gam = gam_df["_display_date"].max() or date.today()

        start_s, end_s = date_filter("seller", dmin_gam, dmax_gam)

        # Extract seller from order_name
        gam_df["seller_ae"] = (
            gam_df["order_name"]
            .str.extract(r"Team-(?:USA|INTL)_([A-Za-z]+)", expand=False)
            .map(AE_NAMES)
        )

        sellers = sorted(gam_df["seller_ae"].dropna().unique())
        selected_seller = st.selectbox(
            "Seller",
            options=sellers,
            key="seller_select",
        ) if sellers else None

        if not selected_seller:
            st.info("No sellers found in order_name — check that order names follow the Team-USA/INTL_Name pattern.")
        else:
            view_gam = gam_df[gam_df["seller_ae"] == selected_seller].copy()

            # Date filter on campaign start_date
            view_gam = view_gam[
                (view_gam["_display_date"] >= start_s)
                & (view_gam["_display_date"] <= end_s)
            ]

            # ---------- Summary metrics ----------
            total_impr = view_gam["impressions_delivered"].sum() if "impressions_delivered" in view_gam else 0
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
            m3.metric("Avg Pacing %", f"{avg_pacing:.1f}%" if avg_pacing is not None else "—")
            m4.metric("Avg Viewability", f"{avg_viewability:.1f}%" if avg_viewability is not None else "—")
            m5.metric("Avg VCR", f"{avg_vcr:.1f}%" if avg_vcr is not None else "—")
            m6.metric("Avg CTR", f"{avg_ctr:.3f}%" if avg_ctr is not None else "—")

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
            display_cols = {
                "line_item_name": "Line Item",
                "order_name": "Order",
                "impressions_goal": "Goal",
                "impressions_delivered": "Delivered",
                "pacing_pct": "Pacing %",
                "ad_server_active_view_viewable_impressions_rate": "Viewability %",
                "vcr": "VCR %",
                "ad_server_ctr": "CTR %",
                "ad_server_cpm_and_cpc_revenue": "Revenue",
            }
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
            if "Delivered" in table_df.columns:
                col_config["Delivered"] = st.column_config.NumberColumn(format="localized")
            if "Pacing %" in table_df.columns:
                col_config["Pacing %"] = st.column_config.NumberColumn(format="%.1f")
            if "Viewability %" in table_df.columns:
                col_config["Viewability %"] = st.column_config.NumberColumn(format="%.1f")
            if "VCR %" in table_df.columns:
                col_config["VCR %"] = st.column_config.NumberColumn(format="%.1f")
            if "CTR %" in table_df.columns:
                col_config["CTR %"] = st.column_config.NumberColumn(format="%.3f")
            if "Revenue" in table_df.columns:
                col_config["Revenue"] = st.column_config.NumberColumn(format="dollar")

            st.dataframe(
                table_df,
                use_container_width=True,
                hide_index=True,
                column_config=col_config,
            )
