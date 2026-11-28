#!/usr/bin/env python3
"""Rebuild the TAG IVT Monitoring Report deck from this year's monthly numbers.

The deck (`templates/tag_ivt_deck_template.pptx`, the 2026 deck as sent
through September) is filled, never redesigned: the slide 4 pre-bid tables,
the slide 9 SIVT tables, and the headline figures that repeat them (cover
"DATA THROUGH …", slide 2 "SIVT x% across <year> to date", slide 5 "% of GA4
pageviews screened", the footnotes' "data through …").

Inputs per month live in Postgres `tag_ivt_months` — typed into the
dashboard's Tools → TAG IVT tab, because neither number has an API here:
GA4 web pageviews and DoubleVerify pre-bid requests (slide 4), and DV
post-bid SIVT rate for display and video (slide 9). Overall / quarterly /
YTD SIVT is weighted by DV post-bid impressions when they're entered, else by
GAM's gross count for the same ad unit (`tag_ivt_gam_pull.pull`; GAM and DV
gross agree within a few %, which can't move a 2-decimal rate).

Run monthly by `tag_ivt_monthly.yml` (Logic App la-sched-yield-tag-ivt, the
4th at 09:00 ET — DV needs ~3 days after month end) and on demand from the
Tools tab. Stores the deck in `campaign_reports` (kind `tag`), writes the GAM
columns CSV for the DV-GAM workbook, and lists any closed month still missing
its DV / GA4 numbers (the workflow emails that list to adops@).

Usage: python scripts/build_tag_ivt_deck.py [--year 2026] [--out DIR] [--no-gam] [--no-store]
"""
from __future__ import annotations

import argparse
import calendar
import json
import os
import sys
from copy import deepcopy
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))
TEMPLATE = REPO / "templates" / "tag_ivt_deck_template.pptx"
TEMPLATE_YEAR = 2026
ET = ZoneInfo("America/New_York")
MONTHS = [calendar.month_abbr[m] for m in range(1, 13)]
DASH = "—"

DDL = """
CREATE TABLE IF NOT EXISTS tag_ivt_months (
    month            DATE PRIMARY KEY,      -- first of the month
    ga4_pageviews    BIGINT,
    dv_prebid        BIGINT,
    sivt_display     DOUBLE PRECISION,      -- fraction, 0.0004 = 0.04%
    sivt_video       DOUBLE PRECISION,
    sivt_overall     DOUBLE PRECISION,      -- optional: DV's own overall rate; else weighted
    dv_display_imps  BIGINT,                -- optional: DV post-bid gross, for weighting
    dv_video_imps    BIGINT,
    note             TEXT,
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
)
"""

# The 2026 deck as sent (data through September) — the starting rows, so the
# first automatic deck matches what TAG already has. Rates are the deck's
# 2-decimal figures.
SEED_OVERALL_2026 = {1: 0.0004, 2: 0.0004, 3: 0.0004, 4: 0.0003, 5: 0.0006, 6: 0.0004,
                     7: 0.0004, 8: 0.0004, 9: 0.0006}
SEED_2026 = {
    1: (88_799_843, 86_029_638, 0.0004, 0.0002), 2: (76_449_175, 73_990_594, 0.0004, 0.0003),
    3: (78_906_939, 76_544_383, 0.0004, 0.0003), 4: (44_598_903, 43_771_981, 0.0003, 0.0002),
    5: (40_298_053, 36_840_158, 0.0007, 0.0002), 6: (39_401_025, 38_439_204, 0.0004, 0.0001),
    7: (41_820_559, 39_544_135, 0.0004, 0.0001), 8: (42_787_009, 40_292_568, 0.0004, 0.0002),
    9: (52_642_347, 50_519_576, 0.0006, 0.0002),
}


# ── pure maths (tested) ──────────────────────────────────────────────────
def _pct(x, dp=2) -> str:
    return DASH if x is None else f"{x * 100:.{dp}f}%"


def _int(x) -> str:
    return DASH if x is None else f"{int(x):,}"


def complete(row: dict | None) -> bool:
    return bool(row) and all(row.get(k) is not None for k in
                             ("ga4_pageviews", "dv_prebid", "sivt_display", "sivt_video"))


def aggregate(rows: dict[int, dict], weights: dict[int, tuple[float, float]]) -> dict:
    """rows: month → inputs (complete months only count). weights: month →
    (display, video) impressions used to weight SIVT. Returns the strings for
    both slides, plus the headline numbers."""
    months = {m: r for m, r in rows.items() if complete(r)}

    def prebid(ms):
        pv = sum(months[m]["ga4_pageviews"] for m in ms)
        dv = sum(months[m]["dv_prebid"] for m in ms)
        return pv, dv, ((pv - dv) / pv if pv else None)

    def sivt(ms):
        d_w = v_w = d_s = v_s = 0.0
        for m in ms:
            r = months[m]
            dw = r.get("dv_display_imps") or weights.get(m, (0, 0))[0]
            vw = r.get("dv_video_imps") or weights.get(m, (0, 0))[1]
            d_w += dw; v_w += vw
            d_s += dw * r["sivt_display"]; v_s += vw * r["sivt_video"]
        disp = d_s / d_w if d_w else (sum(months[m]["sivt_display"] for m in ms) / len(ms))
        vid = v_s / v_w if v_w else (sum(months[m]["sivt_video"] for m in ms) / len(ms))
        tot = (d_s + v_s) / (d_w + v_w) if (d_w + v_w) else (disp + vid) / 2
        return tot, disp, vid

    out = {"month_pre": [], "q_pre": [], "month_sivt": [], "q_sivt": []}
    for m in range(1, 13):
        if m in months:
            pv, dv, disc = prebid([m])
            t, d, v = sivt([m])
            if months[m].get("sivt_overall") is not None:   # DV's own figure wins
                t = months[m]["sivt_overall"]
            out["month_pre"].append([MONTHS[m - 1], _int(pv), _int(dv), _pct(disc)])
            out["month_sivt"].append([MONTHS[m - 1], _pct(t), _pct(d), _pct(v)])
        else:
            out["month_pre"].append([MONTHS[m - 1], DASH, DASH, DASH])
            out["month_sivt"].append([MONTHS[m - 1], DASH, DASH, DASH])
    for q in range(4):
        ms = [m for m in range(3 * q + 1, 3 * q + 4) if m in months]
        if ms:
            pv, dv, disc = prebid(ms)
            t, d, v = sivt(ms)
            out["q_pre"].append([f"Q{q + 1}", _int(pv), _int(dv), _pct(disc)])
            out["q_sivt"].append([f"Q{q + 1}", _pct(t), _pct(d), _pct(v)])
        else:
            out["q_pre"].append([f"Q{q + 1}", DASH, DASH, DASH])
            out["q_sivt"].append([f"Q{q + 1}", DASH, DASH, DASH])
    ms = sorted(months)
    if ms:
        pv, dv, disc = prebid(ms)
        t, d, v = sivt(ms)
        out["q_pre"].append(["YTD", _int(pv), _int(dv), _pct(disc)])
        out["q_sivt"].append(["YTD", _pct(t), _pct(d), _pct(v)])
        out.update(ytd_disc=disc, ytd_sivt=t, through=max(ms),
                   max_month_sivt=max(sivt([m])[0] for m in ms))
    else:
        out["q_pre"].append(["YTD", DASH, DASH, DASH])
        out["q_sivt"].append(["YTD", DASH, DASH, DASH])
        out.update(ytd_disc=None, ytd_sivt=None, through=None, max_month_sivt=None)
    return out


def missing_months(rows: dict[int, dict], year: int, today: date) -> list[str]:
    """Closed months (3+ days past month end, as the runbook says) without all four inputs."""
    out = []
    for m in range(1, 13):
        last = date(year, m, calendar.monthrange(year, m)[1])
        if (today - last).days >= 3 and not complete(rows.get(m)):
            out.append(f"{calendar.month_name[m]} {year}")
    return out


# ── pptx filling ─────────────────────────────────────────────────────────
def _set(tf, text: str) -> None:
    """Replace a text frame's / cell's text, keeping the first run's styling."""
    paras = tf.paragraphs
    runs = paras[0].runs
    if runs:
        runs[0].text = text
        for r in runs[1:]:
            r.text = ""
    else:
        paras[0].text = text
    for p in paras[1:]:
        for r in p.runs:
            r.text = ""


def _fill_table(tbl, rows: list[list[str]]) -> None:
    for i, vals in enumerate(rows, start=1):
        for j, v in enumerate(vals):
            _set(tbl.cell(i, j).text_frame, v)


def fill_deck(agg: dict, year: int, out: Path, template: Path = TEMPLATE) -> None:
    from pptx import Presentation
    prs = Presentation(str(template))
    through = (f"{calendar.month_name[agg['through']]}" if agg["through"] else "")
    ty = str(TEMPLATE_YEAR)

    def walk(slide):
        for sh in slide.shapes:
            yield sh

    for idx, slide in enumerate(prs.slides, start=1):
        tables = [sh for sh in walk(slide) if sh.has_table]
        if idx == 4 and len(tables) >= 2:
            _fill_table(tables[0].table, agg["month_pre"])
            _fill_table(tables[1].table, agg["q_pre"])
        if idx == 9 and len(tables) >= 2:
            _fill_table(tables[0].table, agg["month_sivt"])
            _fill_table(tables[1].table, agg["q_sivt"])
        for sh in walk(slide):
            if not sh.has_text_frame:
                continue
            t = sh.text_frame.text
            new = t
            if t.startswith("DATA THROUGH"):
                new = f"DATA THROUGH {through.upper()} {year}"
            elif t.startswith("SIVT ") and "to date" in t and agg["ytd_sivt"] is not None:
                new = f"SIVT {_pct(agg['ytd_sivt'])} across {year} to date"
            elif t.startswith("DV pre-bid screens") and agg["ytd_disc"] is not None:
                new = f"DV pre-bid screens {round((1 - agg['ytd_disc']) * 100)}% of GA4 pageviews"
            elif "YTD discrepancy vs GA4" in t and agg["ytd_disc"] is not None:
                new = (f"DoubleVerify evaluates each page before any ad is requested. "
                       f"{year} YTD discrepancy vs GA4: {_pct(agg['ytd_disc'])}.")
            elif idx == 5 and t.strip().endswith("%") and len(t.strip()) <= 6 and t.strip() != "100%" \
                    and agg["ytd_disc"] is not None:
                new = _pct(1 - agg["ytd_disc"], 1)
            elif "data through" in t and through:
                import re
                new = re.sub(r"data through \w+", f"data through {through}", t)
            elif "far below the 1% goal" in t and agg["max_month_sivt"] is not None \
                    and agg["max_month_sivt"] >= 0.01:
                new = f"Sophisticated invalid traffic is measured against the 1% goal, month by month, {year}."
            if str(year) != ty:
                new = new.replace(ty, str(year))
            if new != t:
                _set(sh.text_frame, new)
    prs.save(str(out))


# ── IO ───────────────────────────────────────────────────────────────────
def ensure_table(c) -> None:
    """Create tag_ivt_months and, when empty, fill it with the 2026 deck as sent.
    ``c`` is a connection inside a transaction (the dashboard calls this too)."""
    import sqlalchemy as sa
    c.exec_driver_sql(DDL)
    if c.exec_driver_sql("SELECT count(*) FROM tag_ivt_months").scalar():
        return
    for m, (pv, dv, sd, sv) in SEED_2026.items():
        c.execute(sa.text(
            "INSERT INTO tag_ivt_months (month, ga4_pageviews, dv_prebid, sivt_display, "
            "sivt_video, sivt_overall, note) VALUES (:m, :pv, :dv, :sd, :sv, :so, :n)"),
            {"m": date(2026, m, 1), "pv": pv, "dv": dv, "sd": sd, "sv": sv,
             "so": SEED_OVERALL_2026[m], "n": "from the 2026 deck sent through September"})


def engine():
    import sqlalchemy as sa
    e = sa.create_engine(os.environ["DATABASE_URL"], pool_pre_ping=True)
    with e.begin() as c:
        ensure_table(c)
    return e


def load_rows(e, year: int) -> dict[int, dict]:
    import pandas as pd
    import sqlalchemy as sa
    with e.connect() as c:
        df = pd.read_sql(sa.text("SELECT * FROM tag_ivt_months WHERE month >= :a AND month <= :b"),
                         c, params={"a": date(year, 1, 1), "b": date(year, 12, 1)})
    out = {}
    for r in df.to_dict("records"):
        m = pd.Timestamp(r["month"]).month
        out[m] = {k: (None if pd.isna(v) else v) for k, v in r.items()}
    return out


def gam_weights(year: int, through: int):
    import tag_ivt_gam_pull as tg
    df = tg.pull(date(year, 1, 1), date(year, through, calendar.monthrange(year, through)[1]))
    w: dict[int, list[float]] = {}
    for r in df.to_dict("records"):
        m = int(r["month"].split("-")[1])
        tot = r["gam_total"] if r["ad_unit"] != tg.AD_UNITS["video"] else \
            r["ad_exchange"] + r["pmp_pa"] + r["ad_server"]
        slot = 0 if r["ad_unit"] == tg.AD_UNITS["display"] else 1
        w.setdefault(m, [0.0, 0.0])[slot] = float(tot)
    return {m: tuple(v) for m, v in w.items()}, df


def main() -> int:
    ap = argparse.ArgumentParser()
    today = datetime.now(ET).date()
    ap.add_argument("--year", type=int, default=int(os.environ.get("TAG_YEAR") or
                                                    (today.year if today.month > 1 else today.year - 1)))
    ap.add_argument("--out", default="out")
    ap.add_argument("--no-gam", action="store_true")
    ap.add_argument("--no-store", action="store_true")
    a = ap.parse_args()
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)

    e = engine()
    rows = load_rows(e, a.year)
    done = [m for m in rows if complete(rows[m])]
    weights = {}
    if done and not a.no_gam:
        weights, gam_df = gam_weights(a.year, max(done))
        gam_df.to_csv(out / f"tag_ivt_gam_{a.year}.csv", index=False)
    agg = aggregate(rows, weights)
    miss = missing_months(rows, a.year, today)
    deck = out / f"NEWSWEEK_IVT_{a.year}_MONITORING_REPORT.pptx"
    fill_deck(agg, a.year, deck)
    through = f"{a.year}-{agg['through']:02d}" if agg["through"] else f"{a.year}-00"
    summary = (f"Data through {calendar.month_name[agg['through']] if agg['through'] else '—'} {a.year} · "
               f"YTD discrepancy {_pct(agg['ytd_disc'])} · YTD SIVT {_pct(agg['ytd_sivt'])}"
               + (f" · missing: {', '.join(miss)}" if miss else ""))
    (out / "missing.json").write_text(json.dumps(miss))
    print(summary)
    if not a.no_store:
        sys.path.insert(0, str(REPO / "scripts"))
        import campaign_reports_auto as cra
        ce = cra.engine()
        cra.store(ce, {"kind": "tag", "order_id": str(a.year), "line_item_id": "",
                       "order_name": f"TAG IVT {a.year}", "advertiser": "TAG IVT Monitoring Report",
                       "campaign": str(a.year),
                       "start_date": date(a.year, 1, 1),
                       "end_date": date(a.year, agg["through"] or 1,
                                        calendar.monthrange(a.year, agg["through"] or 1)[1]),
                       "file_name": deck.name, "file_bytes": deck.read_bytes(),
                       "summary_md": summary, "note": summary, "status": "ok"})
        print(f"stored {deck.name} (through {through})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
