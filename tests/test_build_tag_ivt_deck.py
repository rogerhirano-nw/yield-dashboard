import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import build_tag_ivt_deck as bt  # noqa: E402


def _seed_rows():
    return {m: {"ga4_pageviews": pv, "dv_prebid": dv, "sivt_display": sd, "sivt_video": sv,
                "sivt_overall": bt.SEED_OVERALL_2026[m]}
            for m, (pv, dv, sd, sv) in bt.SEED_2026.items()}


def test_aggregate_matches_the_deck_as_sent():
    agg = bt.aggregate(_seed_rows(), {})
    assert agg["month_pre"][0] == ["Jan", "88,799,843", "86,029,638", "3.12%"]
    assert agg["month_pre"][9] == ["Oct", "—", "—", "—"]
    assert agg["q_pre"][0] == ["Q1", "244,155,957", "236,564,615", "3.11%"]
    assert agg["q_pre"][2] == ["Q3", "137,249,915", "130,356,279", "5.02%"]
    assert agg["q_pre"][4] == ["YTD", "505,703,853", "485,972,237", "3.90%"]
    assert agg["through"] == 9
    assert agg["month_sivt"][4][2] == "0.07%"          # May display
    # display dominates real weights, so overall follows display
    w = {m: (9.0, 1.0) for m in range(1, 13)}
    agg = bt.aggregate(_seed_rows(), w)
    assert agg["month_sivt"][0] == ["Jan", "0.04%", "0.04%", "0.02%"]
    assert agg["q_sivt"][4][0] == "YTD" and agg["q_sivt"][4][1] == "0.04%"
    assert agg["month_sivt"][4] == ["May", "0.06%", "0.07%", "0.02%"]   # as sent


def test_dv_impressions_beat_gam_weights():
    rows = {1: {"ga4_pageviews": 10, "dv_prebid": 9, "sivt_display": 0.001, "sivt_video": 0.0,
                "dv_display_imps": 1, "dv_video_imps": 1}}
    assert bt.aggregate(rows, {1: (1000.0, 1.0)})["month_sivt"][0][1] == "0.05%"


def test_missing_months():
    rows = _seed_rows()
    assert bt.missing_months(rows, 2026, date(2026, 11, 2)) == []   # 2 days after
    assert bt.missing_months({}, 2026, date(2026, 2, 3))[0] == "January 2026"
    assert bt.missing_months(rows, 2026, date(2026, 11, 3)) == ["October 2026"]
    assert bt.missing_months(rows, 2026, date(2026, 10, 2)) == []


def test_fill_deck(tmp_path):
    pytest.importorskip("pptx")
    from pptx import Presentation
    rows = _seed_rows()
    rows[10] = {"ga4_pageviews": 50_000_000, "dv_prebid": 48_000_000,
                "sivt_display": 0.0005, "sivt_video": 0.0002}
    out = tmp_path / "deck.pptx"
    bt.fill_deck(bt.aggregate(rows, {}), 2026, out)
    p = Presentation(str(out))
    texts = [sh.text_frame.text for s in p.slides for sh in s.shapes if sh.has_text_frame]
    assert "DATA THROUGH OCTOBER 2026" in texts
    tables = [sh.table for sh in p.slides[3].shapes if sh.has_table]
    assert [c.text for c in tables[0].rows[10].cells] == ["Oct", "50,000,000", "48,000,000", "4.00%"]
    assert any("data through October" in t for t in texts)
    # next year: same deck, year swapped, empty tables
    out2 = tmp_path / "deck27.pptx"
    bt.fill_deck(bt.aggregate({}, {}), 2027, out2)
    t2 = [sh.text_frame.text for s in Presentation(str(out2)).slides for sh in s.shapes if sh.has_text_frame]
    assert any("Newsweek IVT 2027 Monitoring Report" in t for t in t2)
