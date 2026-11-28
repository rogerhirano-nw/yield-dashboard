"""The final-report workbook keeps the Sky template's look and carries only
this campaign's data."""
import sys
from pathlib import Path

from openpyxl import load_workbook

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from build_final_report_xlsx import TEMPLATE, build  # noqa: E402

DAYS = [("2026-09-14", 400_000, 400), ("2026-09-15", 350_000, 350), ("2026-09-16", 250_007, 273)]


def _row(imp, clk, **k):
    return {"impressions": imp, "clicks": clk, "revenue": imp * 0.0144, "av_eligible": imp,
            "av_measurable": imp, "av_viewable": round(imp * 0.75), **k}


REPORT = {
    "order": {"id": 4194246183},
    "totals": {"goal": 1_000_000, **_row(1_000_007, 1_023)},
    "by_day": [_row(i, c, date=d) for d, i, c in DAYS],
    "by_creative": [_row(953_381, 989, rendered_creative_size="300x250"),
                    _row(46_626, 34, rendered_creative_size="970x250")],
    "by_device": [_row(845_366, 971, device_category_name="Smartphone"),
                  _row(154_641, 52, device_category_name="Desktop")],
    "by_country": [_row(646_377, 659, country_name="United States"),
                   _row(353_630, 364, country_name="United Kingdom")],
    "dv_attention": {"overall": 133.6, "daily": {"2026-09-14": 130.0, "2026-09-15": 137.2}},
}


def test_template_has_no_client_data():
    wb = load_workbook(TEMPLATE)
    assert wb.sheetnames == ["Summary", "Daily delivery", "Breakdown"]
    assert all(ws.sheet_state == "visible" for ws in wb)
    leaked = [(ws.title, c.coordinate) for ws in wb for row in ws.iter_rows() for c in row
              if any(k in str(c.value) for k in ("Apple", "Sky", "4203010941", "1562", "Ivy"))]
    assert leaked == []


def test_build_fills_template(tmp_path):
    out = tmp_path / "r.xlsx"
    build(REPORT, str(out), name="AHD — Summit", advertiser="AHD", campaign="Summit",
          seller="Amit Shah", flight="14 Sep 2026 – 16 Sep 2026", line_label="Display")
    wb = load_workbook(out)
    s = wb["Summary"]
    assert s["C23"].value == "4194246183"           # text, never 4.19E+09
    assert s["D35"].value == 0.001                   # display CTR target 0.10%
    assert s["F35"].value == "Met the 0.10% Newsweek target"
    assert "134" in s["B13"].value
    assert s["B9"].value == "=D29"                   # template formulas kept
    d = wb["Daily delivery"]
    assert d["B11"].value == "2026-09-16" and d["B12"].value == "Total"
    assert d["C12"].value == "=SUM(C9:C11)"
    assert d["I11"].value is None                    # no DV reading that day
    assert "16 Sep 2026" in d["B14"].value           # footnote names the gap
    b = wb["Breakdown"]
    titles = [c.value for c in b["B"] if c.value in ("By creative size", "By device", "By country")]
    assert titles == ["By creative size", "By device", "By country"]
    assert len(s._images) == 1                       # Newsweek wordmark (no client logo given)


def test_omit_measurement_drops_viewability_and_attention(tmp_path):
    out = tmp_path / "r.xlsx"
    build(REPORT, str(out), name="N — Q127", advertiser="N", campaign="Q127",
          seller="Ivy Lee", flight="30 Oct – 8 Nov", line_label="Interstitial",
          ctr_target=0.003, omit_measurement=True)
    wb = load_workbook(out)
    s = wb["Summary"]
    assert s["F8"].value is None and s["G8"].value is None      # no VIEWABILITY/ATTENTION tiles
    assert s["H27"].value is None and s["I27"].value is None
    assert s["B34"].value == "CTR" and s["D34"].value == 0.003
    assert s["D34"].number_format == s["D35"].number_format or "0.00%" in s["D34"].number_format
    assert "not reported" in s["B39"].value
    texts = [str(c.value) for ws in wb for row in ws.iter_rows() for c in row if c.value]
    assert not any(t in ("VIEWABILITY", "ATTENTION", "VIEWABLE IMPR.", "MEASURABLE IMPR.") for t in texts)
    assert wb["Daily delivery"]["F8"].value is None


def test_near_full_delivery_reads_in_full(tmp_path):
    out = tmp_path / "r.xlsx"
    rep = {**REPORT, "totals": {**REPORT["totals"], "goal": 1_000_030}}  # 23 short
    build(rep, str(out), name="x", advertiser="x", campaign="x", seller="x",
          flight="x", line_label="x")
    assert load_workbook(out)["Summary"]["F33"].value == "Delivered in full"


def test_revenue_columns_in_template_style(tmp_path):
    out = tmp_path / "r.xlsx"
    build(REPORT, str(out), name="x", advertiser="x", campaign="x", seller="x",
          flight="x", line_label="x", omit_measurement=True, revenue=True)
    wb = load_workbook(out)
    s = wb["Summary"]
    assert s["F8"].value == "REVENUE" and s["F9"].value == "=H29"   # tile -> line-item total
    assert s["H27"].value == "REVENUE" and s["H29"].value == "=SUM(H28:H28)"
    assert s["H28"].value == REPORT["totals"]["revenue"]            # unrounded, so totals tie
    d = wb["Daily delivery"]
    assert d["F8"].value == "REVENUE" and d["F12"].value == "=SUM(F9:F11)"
    b = wb["Breakdown"]
    assert [c.value for c in b["F"] if c.value == "REVENUE"] == ["REVENUE"] * 3
