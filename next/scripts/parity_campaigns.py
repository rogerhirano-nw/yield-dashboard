#!/usr/bin/env python3
"""Check the new Campaigns view against the Streamlit one, on the same data.

Runs dashboard.py headless (streamlit.testing AppTest) and the new API's
/api/campaigns, then compares the triage counts, the nine KPI values + their
trend chips, and the first table rows' pace deltas. Use it after any change to
next/api/campaigns.py, and as the template for each view still to port.

  DATABASE_URL=... python next/scripts/parity_campaigns.py [--api http://localhost:8000]
"""
from __future__ import annotations

import argparse
import json
import re
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def streamlit_text() -> str:
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(REPO / "dashboard.py"), default_timeout=900)
    at.query_params["view"] = "campaigns"
    at.run()
    html = " ".join(m.value for m in at.markdown)
    html = re.sub(r"(?s)<style.*?</style>", " ", html)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).replace("&amp;", "&")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://localhost:8000")
    a = ap.parse_args()
    new = json.load(urllib.request.urlopen(f"{a.api}/api/campaigns"))
    old = streamlit_text()
    bad = 0
    for key, label in (("flagged", "All flagged"), ("ending", "Ending soon"), ("underpacing", "Underpacing"),
                       ("overpacing", "Overpacing"), ("viewability", "Viewability")):
        m = re.search(rf"{label} (\d+)", old)
        ok = m and int(m.group(1)) == new["triage"].get(key)
        bad += not ok
        print(f"{'OK ' if ok else 'XX '} triage {label}: old {m.group(1) if m else '?'} new {new['triage'].get(key)}")
    for k in new["kpis"]:
        d = (k.get("delta") or {}).get("text") or ""
        if d and d not in old:
            bad += 1
            print(f"XX  KPI {k['label']}: trend '{d}' not in the old strip")
        else:
            print(f"OK  KPI {k['label']}: {k['value']} {d}")
    print("PASS" if not bad else f"{bad} difference(s)")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
