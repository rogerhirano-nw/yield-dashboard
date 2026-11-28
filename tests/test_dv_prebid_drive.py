import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import dv_prebid_drive as dpd  # noqa: E402

SEP = [1738177, 1597193, 1898842, 1873518, 1556364, 1229813, 2176050, 1574757, 2255782,
       2731900, 2074456, 2067773, 1656120, 1849676, 2325656, 1739483, 1741083, 1442202,
       1221962, 1473735, 1301281, 1774550, 1447523, 1227372, 1371197, 1815734, 1326151,
       1229287, 1433874, 1368065]


def test_september_matches_the_deck():
    text = "﻿Day,Total\n" + "".join(f"2026-09-{i:02d},{v}\n" for i, v in enumerate(SEP, 1))
    assert dpd.month_total(text, 2026, 9) == (50_519_576, 30)   # deck: 50,519,576


def test_other_months_and_blanks_ignored():
    text = "Day,Total\n2026-09-30,5\n2026-10-01,\"1,000\"\n,\n2026-10-02,7\n"
    assert dpd.month_total(text, 2026, 10) == (1007, 2)


def test_name_pattern():
    assert dpd.NAME_RE.match("report_2026-10.csv").groups() == ("2026", "10")
    assert not dpd.NAME_RE.match("report_2026-10 (1).csv")
