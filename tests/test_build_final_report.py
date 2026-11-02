"""The final-report workbook keeps the Sky template's look and carries only
this campaign's data."""
import sys
from pathlib import Path

from openpyxl import load_workbook

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from build_final_report_xlsx import TEMPLATE, build  # noqa: E402

DAYS = [("2026-09-14", 400_000, 400), ("2026-09-15", 350_000, 350), ("2026-09-16", 250_007, 273)]


def _row(imp, clk, **k):
    return {"impressions": imp, "clicks": clk, "av_eligible": imp,
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
