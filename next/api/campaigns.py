"""Campaigns view — Direct (GAM) line items, KPIs and triage.

Ported from dashboard.py's Campaigns/Direct section (lines ~3340–6184 of the
Streamlit app; spec in docs/next_app.md). Decisions still come from
dashboard_logic (``dl``) so both apps grade a line identically.

Split by cost:
- ``_prepared(store)`` — everything per line that doesn't depend on filters
  (derived columns, DV joins, bands, deltas). Built once per data version.
- ``/api/campaigns`` — applies filters + triage to that frame, sums the KPIs.
  ~3.5k rows, so a request is milliseconds.
"""
from __future__ import annotations

import math
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from fastapi import APIRouter, Query

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import dashboard_logic as dl  # noqa: E402

from .data import Store, settings, store  # noqa: E402

router = APIRouter()
ET = ZoneInfo("America/New_York")
EXCLUDED_ORDER_IDS = {"3648897741", "4082002976"}
DAYS = range(7, 0, -1)  # 7d … 1d, oldest first


def today_et() -> date:
    return datetime.now(ET).date()


def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) or math.isinf(f) else f


# ── DV (per-line lookups, same functions as the Streamlit app) ────────────
def _dv(st: Store) -> dict:
    def build(s: Store):
        out = {"attn_col": "line_item_name", "attn": {}, "attn_prior": {}, "attn_series": {},
               "ivt_col": "line_item_name", "sivt": {}, "sivt_prior": {}, "givt": {}, "givt_prior": {},
               "sivt_series": {}, "givt_series": {}, "attn_by_date": [], "ivt_by_date": {}}
        cut = pd.Timestamp(today_et() - timedelta(days=30))
        a = s.get("dv_attention")
        if not a.empty:
            a = a.copy()
            a["date"] = pd.to_datetime(a["date"], errors="coerce")
            a = a[(a["date"] >= cut) & a["attention_index"].notna()]
            a["attention_index"] = pd.to_numeric(a["attention_index"], errors="coerce")
            a["line_item_id"] = a["line_item_id"].astype("string").str.replace(r"\.0$", "", regex=True)
            by_li = (a.dropna(subset=["line_item_id"]).groupby(["line_item_id", "date"], as_index=False)
                     .agg(attention_index=("attention_index", "mean"), line_item_name=("line_item_name", "min")))
            col = dl.choose_join_col(by_li)
            out["attn_col"] = col
            cur, prior = dl.attention_current_and_prior(by_li, col)
            out["attn"], out["attn_prior"] = cur, prior
            out["attn_series"] = dl.attention_daily_series_by_li(by_li, col)
            by_date = a.groupby("date")["attention_index"].mean().sort_index()
            out["attn_by_date"] = [float(x) for x in by_date.tail(7)]
            out["attn_mean"] = float(by_date.mean()) if len(by_date) else None
        v = s.get("dv_ivt")
        if not v.empty:
            v = v.copy()
            v["date"] = pd.to_datetime(v["date"], errors="coerce")
            v = v[v["date"] >= cut]
            v["monitored_ads"] = pd.to_numeric(v["monitored_ads"], errors="coerce").fillna(0)
            v["line_item_id"] = v["line_item_id"].astype("string").str.replace(r"\.0$", "", regex=True)
            g = (v.groupby(["line_item_id", "order_name", "date", "traffic_validity"], dropna=False,
                           as_index=False).agg(monitored_ads=("monitored_ads", "sum"),
                                               line_item_name=("line_item_name", "min")))
            col = dl.choose_join_col(g)
            out["ivt_col"] = col
            for k, lab in (("sivt", "Fraud/SIVT"), ("givt", "Fraud/GIVT")):
                cur, prior = dl.ivt_share_with_prior(g, col, lab)
                out[k], out[f"{k}_prior"] = cur, prior
                out[f"{k}_series"] = dl.ivt_daily_series_by_li(g, col, lab)
            tot = v.groupby("date")["monitored_ads"].sum()
            for k, lab in (("sivt", "Fraud/SIVT"), ("givt", "Fraud/GIVT")):
                fr = v[v["traffic_validity"] == lab].groupby("date")["monitored_ads"].sum()
                ser = (fr.reindex(tot.index).fillna(0) / tot.replace(0, np.nan) * 100).dropna()
                out["ivt_by_date"][k] = [float(x) for x in ser.sort_index().tail(7)]
                out["ivt_by_date"][f"{k}_now"] = (float(fr.sum() / tot.sum() * 100)
                                                  if tot.sum() else None)
        return out
    return st.derived("dv", build)


def _key(row, col):
    if col == "line_item_id":
        return str(row.get("line_item_id") or "")
    return re.sub(r"^#\d+\s+", "", str(row.get("line_item_name") or ""))


# ── prepared frame ───────────────────────────────────────────────────────
def _prepared(st: Store) -> pd.DataFrame:
    return st.derived("campaigns", _build)


def _build(st: Store) -> pd.DataFrame:
    cfg = settings(st)
    df = st.get("gam_campaigns")
    if df.empty:
        return df
    df = df.copy()
    df = df[~df["order_id"].astype(str).str.replace(r"\.0$", "", regex=True).isin(EXCLUDED_ORDER_IDS)]
    df["line_item_id"] = df["line_item_id"].astype(str).str.replace(r"\.0$", "", regex=True)
    df["order_id"] = df["order_id"].astype(str).str.replace(r"\.0$", "", regex=True)

    if "video_ad_duration" in df:
        df["_creative_max_dur"] = pd.to_numeric(df["video_ad_duration"], errors="coerce")
    else:
        df["_creative_max_dur"] = np.nan

    prefixes = [p.rstrip("%") for p in cfg.get("included_order_patterns", ["Newsweek_Direct%"]) if p]
    has_order = df["order_name"].notna() & (df["order_name"] != "")
    if prefixes:
        mo = pd.Series(False, index=df.index)
        ml = pd.Series(False, index=df.index)
        for p in prefixes:
            mo |= has_order & df["order_name"].str.startswith(p, na=False)
            ml |= (~has_order) & df["line_item_name"].str.startswith(p, na=False)
        df = df[mo | ml]

    for c in ("start_date", "end_date"):
        df[c] = pd.to_datetime(df[c], errors="coerce").dt.date
    numeric = [c for c in df.columns if re.search(
        r"(_\dd$|^lifetime_|^ad_server_|pacing_pct|impressions_goal|cpm_rate|impressions_delivered|"
        r"video_interaction|^vcr$)", c)]
    for c in numeric:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    if {"video_interaction_video_starts", "video_interaction_video_completions"} <= set(df):
        s = df["video_interaction_video_starts"]
        df["vcr"] = (df["video_interaction_video_completions"] / s * 100).where(s > 0)

    ae = cfg.get("ae_names", {})
    sp = df["salesperson"].apply(dl.parse_gam_salesperson) if "salesperson" in df else pd.Series(pd.NA, index=df.index)
    reg = df["order_name"].str.extract(dl.AE_TOKEN_RE, expand=False).map(ae)
    li = df["line_item_name"].str.extract(dl.AE_TOKEN_RE, expand=False).map(ae)
    df["seller_ae"] = sp.where(sp.notna(), reg.fillna(li))
    df["advertiser"] = df["line_item_name"].apply(dl.li_part, idx=7).str.replace("-", " ", regex=False)
    df["campaign_name"] = df["line_item_name"].apply(dl.li_part, idx=8).str.replace("-", " ", regex=False)
    aliases = cfg.get("format_aliases") or {}
    api_fmt = df["inventory_format_name"] if "inventory_format_name" in df else pd.Series([None] * len(df), index=df.index)
    df["ad_format"] = [dl.derive_format(a, n, aliases) for a, n in zip(api_fmt, df["line_item_name"])]
    df["team"] = df["line_item_name"].str.extract(dl.TEAM_TOKEN_RE, expand=False).map(
        cfg.get("team_names", {"USA": "USA", "INTL": "International"}))
    for c in ("advertiser", "campaign_name", "ad_format", "seller_ae", "team"):
        df[c] = df[c].replace({None: pd.NA, "None": pd.NA, "": pd.NA})
    df["_bench_format"] = [dl.bump_video_format(f, d) for f, d in zip(df["ad_format"], df["_creative_max_dur"])]
    rules = cfg.get("long_preroll_lines") or []
    if rules:
        m = df.apply(lambda r: dl.matches_long_preroll(r, rules), axis=1)
        df.loc[m, "_bench_format"] = dl.LONG_PREROLL_FORMAT

    # per-line values (lifetime first, the Streamlit overrides at 4728+)
    def ratio(n, d):
        if n in df and d in df:
            return (df[n] / df[d] * 100).where(df[d] > 0)
        return pd.Series(np.nan, index=df.index)
    df["revenue"] = df.get("lifetime_revenue", pd.Series(np.nan, index=df.index)).fillna(
        df.get("ad_server_cpm_and_cpc_revenue"))
    df["viewability"] = ratio("lifetime_viewable_imps", "lifetime_measurable_imps")
    df["ctr"] = ratio("lifetime_clicks", "lifetime_impressions_delivered")
    df["vcr_lt"] = ratio("lifetime_video_completes", "lifetime_video_starts").fillna(df.get("vcr"))
    goal = df.get("impressions_goal")
    lt = df.get("lifetime_impressions_delivered")
    df["remaining"] = (goal - lt).clip(lower=0).where(goal.notna() & lt.notna())
    df["progress"] = (lt / goal).clip(upper=1.0).where(goal > 0)
    t = pd.Timestamp(today_et())  # dashboard.py passes a Timestamp
    df["pacing_prior"] = [dl.prior_pacing(g, l, d1, s, e, t) for g, l, d1, s, e in zip(
        goal, lt, df["impressions_1d"], df["start_date"], df["end_date"])]

    target = float(cfg.get("pacing_target_pct", 100.0))
    bench = cfg.get("benchmarks_by_format", {}) or {}
    rows = []
    dv = _dv(st)
    for r in df.to_dict("records"):
        fmt = r.get("_bench_format")
        p = _num(r.get("pacing_pct"))
        vt = dl.bench_target(bench, fmt, "viewability_pct", "Display", 70.0)
        vr = dl.bench_red_cut(bench, fmt, "viewability", vt, "Display")
        ct = dl.bench_target(bench, fmt, "ctr_pct", "Display", None)
        cr = dl.bench_red_cut(bench, fmt, "ctr", ct, "Display") if ct else None
        is_video = "video" in str(fmt or "").lower()
        vct = dl.bench_target(bench, fmt, "vcr_pct", "Video", 60.0)
        vcr_red = dl.bench_red_cut(bench, fmt, "vcr", vct, "Video")
        ak, ik = _key(r, dv["attn_col"]), _key(r, dv["ivt_col"])
        v = _num(r.get("viewability"))
        c = _num(r.get("ctr"))
        vc = _num(r.get("vcr_lt")) if is_video else None
        rows.append({
            "pace_band": dl.pace_band(p, target) if p is not None else None,
            "is_new": bool(dl.is_new_line_item(r.get("lifetime_impressions_delivered"), r.get("impressions_1d"))),
            "view_target": vt, "view_band": dl.band(v, vt, vr) if v is not None else None,
            "view_prior": dl.lt_minus_1d_ratio(r.get("lifetime_viewable_imps"), r.get("viewable_imps_1d"),
                                               r.get("lifetime_measurable_imps"), r.get("measurable_imps_1d")),
            "ctr_target": ct, "ctr_band": dl.band(c, ct, cr) if (c is not None and ct) else None,
            "ctr_prior": dl.lt_minus_1d_ratio(r.get("lifetime_clicks"), r.get("clicks_1d"),
                                              r.get("lifetime_impressions_delivered"), r.get("impressions_1d")),
            "is_video": is_video, "vcr_cell": vc, "vcr_target": vct,
            "vcr_band": dl.band(vc, vct, vcr_red) if vc is not None else None,
            "vcr_prior": dl.lt_minus_1d_ratio(r.get("lifetime_video_completes"), r.get("video_completes_1d"),
                                              r.get("lifetime_video_starts"), r.get("video_starts_1d")) if is_video else None,
            "attention": dv["attn"].get(ak), "attention_prior": dv["attn_prior"].get(ak),
            "attention_band": dl.attention_band(dv["attn"].get(ak)) if dv["attn"].get(ak) is not None else None,
            "sivt": dv["sivt"].get(ik), "sivt_prior": dv["sivt_prior"].get(ik),
            "givt": dv["givt"].get(ik), "givt_prior": dv["givt_prior"].get(ik),
            "sivt_band": dl.ivt_band(dv["sivt"].get(ik)) if dv["sivt"].get(ik) is not None else None,
            "givt_band": dl.ivt_band(dv["givt"].get(ik)) if dv["givt"].get(ik) is not None else None,
            "rev_delta": dl.volume_pct_delta(r.get("lifetime_revenue"), r.get("revenue_1d")),
            "imp_delta": dl.volume_pct_delta(r.get("lifetime_impressions_delivered"), r.get("impressions_1d")),
            "series_attention": dv["attn_series"].get(ak), "series_sivt": dv["sivt_series"].get(ik),
            "series_givt": dv["givt_series"].get(ik),
        })
    extra = pd.DataFrame(rows, index=df.index)
    df = pd.concat([df, extra], axis=1)

    # A–Z by display name, #N per order after the sort (dashboard.py 4967–4986)
    df["display_name"] = df["line_item_name"].apply(dl.line_item_display_name)
    df["_sort"] = df["display_name"].str.casefold()
    df = df.sort_values(["_sort", "line_item_id"], kind="stable", na_position="last")
    grp = df["order_id"].where(df["order_id"].notna() & (df["order_id"] != ""), df["order_name"])
    n_in_order = grp.map(grp.value_counts())
    df["ordinal"] = (df.groupby(grp, dropna=False).cumcount() + 1).where(n_in_order > 1)
    return df


# ── triage + KPIs on the filtered frame ──────────────────────────────────
def _triage(v: pd.DataFrame, cfg: dict) -> dict[str, set]:
    target = float(cfg.get("pacing_target_pct", 100.0))
    crit, high = target * 0.75, target * 1.10
    p = pd.to_numeric(v["pacing_pct"], errors="coerce")
    vb = float(((cfg.get("benchmarks_by_format") or {}).get("Display") or {}).get("viewability_pct") or 70.0)
    out = {"underpacing": set(v.index[p < crit]), "overpacing": set(v.index[p > high]),
           "viewability": set(v.index[v["viewability"] < vb]) if "viewability" in v else set()}
    win = int(cfg.get("landing_window_days", 7))
    thr = float(cfg.get("landing_threshold_pct", 100.0))
    t = today_et()
    ending = set()
    for i, r in v.iterrows():
        e = r.get("end_date")
        if not isinstance(e, date):
            continue
        days = (e - t).days
        proj = dl.landing_projection(r.get("impressions_goal"), r.get("lifetime_impressions_delivered"),
                                     r.get("impressions_1d"), days)
        if proj and dl.landing_at_risk(days, proj.get("projected_pct") if isinstance(proj, dict) else proj[1],
                                       win, thr):
            ending.add(i)
    out["ending"] = ending
    return out


def _series_sum(v, prefix):
    cols = [f"{prefix}_{n}d" for n in DAYS]
    if not all(c in v for c in cols):
        return None
    return [float(v[c].sum()) for c in cols]


def _ratio_series(v, num, den):
    a, b = _series_sum(v, num), _series_sum(v, den)
    if not a or not b:
        return None
    return [x / y * 100 if y else None for x, y in zip(a, b)]


def _pacing_series(v):
    lt = v.get("lifetime_impressions_delivered")
    if lt is None or "impressions_goal" not in v:
        return None
    t = today_et()
    out = []
    for n in DAYS:
        cum = lt.copy()
        for k in range(1, n):
            if f"impressions_{k}d" in v:
                cum = cum - v[f"impressions_{k}d"].fillna(0)
        cum = cum.clip(lower=0)
        as_of = t - timedelta(days=n)
        exp = []
        for s, e, g in zip(v["start_date"], v["end_date"], v["impressions_goal"]):
            if not isinstance(s, date) or not isinstance(e, date) or not g or g != g:
                exp.append(0.0)
                continue
            total = max((e - s).days, 1)
            el = min(max((as_of - s).days, 0), total)
            exp.append(g * el / total)
        exp = pd.Series(exp, index=v.index)
        m = exp > 0
        out.append(float(cum[m].sum() / exp[m].sum() * 100) if exp[m].sum() else None)
    return out


def _kpis(v: pd.DataFrame, st: Store, cfg: dict) -> list[dict]:
    dv = _dv(st)
    bench = cfg.get("benchmarks_by_format", {}) or {}
    vb = float((bench.get("Display") or {}).get("viewability_pct") or 70.0)
    target = float(cfg.get("pacing_target_pct", 100.0))

    def tile(key, label, value, series, fmt, target_line=None, sub=None, invert=False, lead=False):
        d = dl.trend_delta([x for x in (series or []) if x is not None], fmt, invert) if series else None
        return {"key": key, "label": label, "value": value, "series": series, "target": target_line,
                "delta": d, "sub": (d or {}).get("basis") if d and d.get("basis") else sub, "lead": lead}

    s = lambda c: float(v[c].sum()) if c in v else None  # noqa: E731
    lv, lm = s("lifetime_viewable_imps"), s("lifetime_measurable_imps")
    li, lc = s("lifetime_impressions_delivered"), s("lifetime_clicks")
    vids = v[v["ad_format"].astype(str).str.contains("video", case=False, na=False)]
    # value + series over the whole filtered set, as dashboard.py 3811 / 4492
    vs, vcmp = (s("lifetime_video_starts") or 0, s("lifetime_video_completes") or 0)
    ctr_t = (bench.get("Display") or {}).get("ctr_pct")
    vcr_t = (bench.get("Video") or {}).get("vcr_pct") or 70
    ivt = dv["ivt_by_date"]
    return [
        tile("revenue", "Revenue", s("ad_server_cpm_and_cpc_revenue"), _series_sum(v, "revenue"), "pct",
             sub="vs 7-day avg", lead=True),
        tile("pacing", "Avg pacing", _num(pd.to_numeric(v["pacing_pct"], errors="coerce").mean()),
             _pacing_series(v), "pp", target, f"target {int(target)}%"),
        tile("impressions", "Impressions", li, _series_sum(v, "impressions"), "pct", sub="vs 7-day avg"),
        tile("viewability", "Viewability", (lv / lm * 100) if lm else None,
             _ratio_series(v, "viewable_imps", "measurable_imps"), "pp", vb, f"target {vb:g}%"),
        tile("attention", "Attention", dv.get("attn_mean"), dv["attn_by_date"] or None, "pp", 100, "target 100"),
        tile("sivt", "SIVT", ivt.get("sivt_now"), ivt.get("sivt"), "pp", 1.0, "target ≤1%", invert=True),
        tile("givt", "GIVT", ivt.get("givt_now"), ivt.get("givt"), "pp", 1.0, "target ≤1%", invert=True),
        tile("vcr", "VCR", ((vcmp / vs * 100) if vs else None) if len(vids) else None,
             _ratio_series(v, "video_completes", "video_starts") if len(vids) else None, "pp", vcr_t,
             f"target {vcr_t:g}% · {len(vids)} video line{'s' if len(vids) != 1 else ''}" if len(vids) else "No video"),
        tile("ctr", "CTR", (lc / li * 100) if li else None, _ratio_series(v, "clicks", "impressions"), "pp",
             None, f"target {ctr_t:g}%" if ctr_t else "—"),
    ]


ROW_FIELDS = [
    "line_item_id", "line_item_name", "display_name", "ordinal", "order_id", "order_name", "advertiser",
    "campaign_name", "ad_format", "cpm_rate", "seller_ae", "team", "status", "line_item_type",
    "start_date", "end_date", "impressions_goal", "lifetime_impressions_delivered", "remaining", "progress",
    "revenue", "rev_delta", "imp_delta", "pacing_pct", "pacing_prior", "pace_band", "is_new",
    "viewability", "view_prior", "view_band", "view_target", "attention", "attention_prior", "attention_band",
    "sivt", "sivt_prior", "sivt_band", "givt", "givt_prior", "givt_band", "ctr", "ctr_prior", "ctr_band",
    "ctr_target", "is_video", "vcr_cell", "vcr_prior", "vcr_band", "vcr_target", "lifetime_clicks",
    "_creative_max_dur", "deal_id",
]


def _clean(x):
    if isinstance(x, (date, datetime)):
        return x.isoformat()
    if isinstance(x, np.bool_):
        return bool(x)
    if isinstance(x, (np.integer,)):
        return int(x)
    if isinstance(x, (float, np.floating)):
        return None if (math.isnan(x) or math.isinf(x)) else float(x)
    if x is pd.NA or x is pd.NaT:
        return None
    if isinstance(x, (list, tuple, np.ndarray)):
        return [_clean(y) for y in x]
    return x


@router.get("/api/campaigns")
def campaigns(seller: str = "All", advertiser: list[str] = Query([]), format: list[str] = Query([]),
              status: list[str] | None = Query(None), team: list[str] = Query([]),
              manager: list[str] = Query([]), triage: str = ""):
    st = store()
    cfg = settings(st)
    df = _prepared(st)
    if df.empty:
        return {"rows": [], "kpis": [], "triage": {}, "options": {}, "total": 0}
    status_opts = sorted(df["status"].dropna().unique())
    defaults = [x for x in cfg.get("default_statuses", ["Delivering", "Upcoming"]) if x in status_opts]
    if status is None:
        status = defaults
    ae, am = cfg.get("ae_names", {}), cfg.get("account_managers", {}) or {}
    am_by_name = {ae.get(code, code): a for code, a in am.items() if a}

    v = df
    if seller and seller != "All":
        v = v[v["seller_ae"] == seller]
    for col, sel in (("advertiser", advertiser), ("ad_format", format), ("status", status), ("team", team)):
        if sel:
            v = v[v[col].isin(sel)]
    if manager:
        v = v[v["seller_ae"].fillna("").map(am_by_name).fillna("Unassigned").replace("", "Unassigned").isin(manager)]

    tri = _triage(v, cfg)
    flagged = set().union(*tri.values()) if tri else set()
    kpis = _kpis(v, st, cfg)
    shown = v.loc[v.index.isin(tri[triage])] if triage in tri else v

    pmp = st.get("pubmatic_deals")
    pmp_sellers = (set(pmp["deal"].str.extract(r"Team-(?:USA|INTL)_([A-Za-z]+)", expand=False).map(ae).dropna())
                   if not pmp.empty and "deal" in pmp else set())
    has_unmapped = bool(df["seller_ae"].dropna().map(lambda s: s not in am_by_name).any())
    options = {
        "seller": ["All"] + sorted(set(df["seller_ae"].dropna()) | pmp_sellers),
        "advertiser": sorted(df["advertiser"].dropna().unique()),
        "format": sorted(df["ad_format"].dropna().unique()),
        "status": status_opts, "status_default": defaults,
        "team": sorted(df["team"].dropna().unique()),
        "manager": sorted({a for a in am_by_name.values() if a}) + (["Unassigned"] if has_unmapped else []),
    }
    cols = [c for c in ROW_FIELDS if c in shown]
    rows = [{k: _clean(x) for k, x in zip(cols, rec)} for rec in shown[cols].itertuples(index=False, name=None)]
    pulled = df["_pulled_at"].max() if "_pulled_at" in df else None
    return {
        "rows": rows, "total": int(len(v)), "shown": int(len(shown)),
        "kpis": [{k: _clean(x) if not isinstance(x, dict) else x for k, x in t.items()} for t in kpis],
        "triage": {k: len(s) for k, s in tri.items()} | {"flagged": len(flagged)},
        "options": options, "applied_status": status,
        "pulled_at": str(pulled) if pulled is not None else None,
        "pacing_target": float(cfg.get("pacing_target_pct", 100.0)),
        "gam_network_id": cfg.get("gam_network_id") or "",
    }


@router.get("/api/campaigns/{line_item_id}")
def line_item(line_item_id: str):
    """Drawer detail: daily series + the row."""
    st = store()
    df = _prepared(st)
    hit = df[df["line_item_id"] == str(line_item_id)]
    if hit.empty:
        return {"error": "not found"}
    r = hit.iloc[0]
    series = {}
    for name, a, b in (("viewability", "viewable_imps", "measurable_imps"), ("ctr", "clicks", "impressions"),
                       ("vcr", "video_completes", "video_starts")):
        if all(f"{a}_{n}d" in r for n in DAYS):
            series[name] = [(_num(r[f"{a}_{n}d"]) / _num(r[f"{b}_{n}d"]) * 100)
                            if _num(r.get(f"{b}_{n}d")) else None for n in DAYS]
    series["impressions"] = [_num(r.get(f"impressions_{n}d")) for n in DAYS]
    for k in ("series_attention", "series_sivt", "series_givt"):
        series[k.replace("series_", "")] = r.get(k)
    t = today_et()
    return {"row": {k: _clean(r.get(k)) for k in ROW_FIELDS if k in r},
            "series": {k: _clean(list(v)) if v is not None else None for k, v in series.items()},
            "dates": [(t - timedelta(days=n)).isoformat() for n in DAYS]}
