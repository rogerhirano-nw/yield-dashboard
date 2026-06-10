"""DV CSV parsers must emit line_item_id as clean integer strings.

Regression test for the ".0" join-key bug: open-exchange rows have a blank
Line Item ID, which makes pandas parse the whole column as float64, and a
bare astype(str) then yields "7306352098.0" — which never matches
gam_campaigns' integer-string IDs. That broke the ID-based DV↔GAM join on
both the dashboard (Attention/SIVT/GIVT showed "—" for every Direct line)
and the ingest validator (62 false "no GAM match" warnings per sweep).
"""

from __future__ import annotations

import pandas as pd

from dv_attention_client import parse_dv_csv
from dv_ivt_client import parse_dv_ivt_csv

# One direct row plus one open-exchange row with blank Line Item / Line Item
# ID — the blank is what forces the float64 parse this test guards against.
ATTENTION_CSV = b"""Date,Order,Line Item,Line Item ID,Attention Index
2026-06-09,Order A,#7306352098 Newsweek_Direct_Gambling_Spinfinite,7306352098,104.2
2026-06-09,Open Exchange,,,98.0
"""

IVT_CSV = b"""Traffic Validity,Date,Advertiser,Order,Line Item,Line Item ID,Fraud/SIVT Rate,GIVT Rate,IVT-Rate,Monitored Ads
Valid Traffic,2026-06-09,Adv,Order A,#7306352098 Newsweek_Direct_Gambling_Spinfinite,7306352098,0,0,0,1000
Fraud/SIVT,2026-06-09,Adv,Open Exchange,,,1,0,1,5
"""


def _assert_normalized(ids: list) -> None:
    assert ids[0] == "7306352098"
    # The blank cell must be null-ish — None on older pandas, NaN on ≥2.2
    # (replace() downcasts None to NaN on object columns). Both fall out of
    # dropna() and write as SQL NULL, so either is correct here.
    assert pd.isna(ids[1])


def test_attention_line_item_id_has_no_float_suffix():
    df = parse_dv_csv(ATTENTION_CSV)
    _assert_normalized(df["line_item_id"].tolist())


def test_ivt_line_item_id_has_no_float_suffix():
    df = parse_dv_ivt_csv(IVT_CSV)
    _assert_normalized(df["line_item_id"].tolist())
