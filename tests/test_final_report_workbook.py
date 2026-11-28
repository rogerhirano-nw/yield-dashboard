import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import final_report_workbook as frw  # noqa: E402


def test_derive_from_order_name():
    rep = {"order": {"name": "Newsweek_Direct_Health_NA_NA_NA_NA_American-Hospital-Dubai_"
                             "Interview-AI-Health-Summit_AE_Display_SO01122_Team-International_AShah",
                     "advertiser": "[nw] AHD"},
           "line_items": [{"name": "x_Display_y", "start": "2026-09-14 00:00", "end": "2026-10-26 23:59"},
                          {"name": "x_Display_z", "start": "2026-09-20 00:00", "end": "2026-11-02 23:59"}],
           "by_creative": [{"rendered_creative_size": "970x250"}, {"rendered_creative_size": "300x250"},
                           {"rendered_creative_size": "Native"}]}
    a = frw.derive(rep, {"AShah": "Amit Shah"})
    assert a["advertiser"] == "American Hospital Dubai"
    assert a["campaign"] == "Interview AI Health Summit"
    assert a["seller"] == "Amit Shah"
    assert a["flight"] == "14 Sep 2026 – 2 Nov 2026"
    assert a["line_label"] == "Display · 300x250, 970x250"
    assert a["ctr_target"] == 0.001


def test_derive_interstitial_and_odd_names():
    rep = {"order": {"name": "AppleTv Nocturne", "advertiser": "[nw] Omnicom"},
           "line_items": [{"name": "Newsweek_Direct_x_Interstitial_SO1", "start": "—", "end": "—"}],
           "by_creative": []}
    a = frw.derive(rep, {})
    assert a["advertiser"] == "[nw] Omnicom" and a["campaign"] == "AppleTv Nocturne"
    assert a["flight"] == "" and a["ctr_target"] == 0.003
    assert a["line_label"].startswith("Interstitial")


def test_derive_falls_back_to_line_item_name():
    # the real AHD report: the JSON's order name is only the id
    rep = {"order": {"name": "4194246183", "advertiser": "[nw] Investment-Reports"},
           "line_items": [{"name": "Newsweek_Direct_Health_NA_NA_NA_Investment-Reports_"
                                   "Americal-Hospital-Dubai_American-Hospital-Dubai-Interview--AI-"
                                   "Health-Summit-2026_Global_Display_IO1172-2_Team-INTL_AShah",
                           "start": "2026-09-14 00:00", "end": "2026-11-02 23:59"}],
           "by_creative": []}
    a = frw.derive(rep, {"AShah": "Amit Shah"})
    assert a["advertiser"] == "Americal Hospital Dubai"
    assert a["campaign"] == "American Hospital Dubai Interview AI Health Summit 2026"
    assert a["seller"] == "Amit Shah"
