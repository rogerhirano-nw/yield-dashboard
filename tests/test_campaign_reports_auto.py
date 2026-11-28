import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import campaign_reports_auto as cra  # noqa: E402


def test_default_window():
    assert cra.default_window("final", date(2026, 11, 28)) == (date(2026, 11, 27), date(2026, 11, 27))
    assert cra.default_window("deck", date(2026, 11, 28)) == (date(2026, 11, 26), date(2026, 11, 28))


def test_filters():
    assert cra.is_report_order("1", "Newsweek_Direct_Health_x")
    assert cra.is_report_order("1", "Newsweek_PG_Gambling_x")
    assert not cra.is_report_order("1", "Newsweek_PD_x")
    assert not cra.is_report_order("4082002976", "Newsweek_Direct_Test")
    assert cra.is_on_site_line("Newsweek_Direct_Tech_x_Display_SO1")
    assert not cra.is_on_site_line("Newsweek_Direct_Tech_x_Apple-News_SO1")
    assert not cra.is_on_site_line("Newsweek_Direct_x_Newsletter-Bulletin_SO1")


def test_name_parts():
    n = "Newsweek_Direct_Health_NA_NA_NA_NA_Elevance-Health_AI--Health-Summit_US_Display_SO1_Team-USA_AShah"
    assert cra.name_parts(n) == ("Elevance Health", "AI Health Summit")
    assert cra.name_parts("short") == ("", "short")
