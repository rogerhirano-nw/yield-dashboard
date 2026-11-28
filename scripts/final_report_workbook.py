#!/usr/bin/env python3
"""Fill the client workbook straight from the final-report JSON, no hand-typed flags.

`build_final_report_xlsx.py` needs the advertiser, campaign, seller, flight and
line label spelled out. On the dashboard's Tools tab nobody should have to
type what the order name already says, so this derives each one and lets the
person override any of them (env `FR_ADVERTISER`, `FR_CAMPAIGN`, `FR_SELLER`,
`FR_FLIGHT`, `FR_LINE_LABEL`, `FR_CTR_TARGET` as a percentage, e.g. `0.10`,
`FR_OMIT_MEASUREMENT`, `FR_REVENUE` = `true`):

- advertiser / campaign: order-name tokens 7 / 8 (the naming standard, SOP 1.3)
- seller: the last token, through settings.json `ae_names` (RShore → Rob Shore)
- flight: earliest line start → latest line end, "14 Sep 2026 – 26 Oct 2026"
- line label: canonical format + rendered sizes, "Display · 300x250, 970x250"

Usage: python scripts/final_report_workbook.py final_report.json OUT_DIR
Prints the workbook path. Runbook: docs/final_report.md.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

import dashboard_logic as dl  # noqa: E402


def _words(tok: str) -> str:
    return " ".join(tok.replace("-", " ").split())


def convention_name(report: dict) -> str:
    """The first name that follows the 14-field standard: the order's, else its
    first line item's. The report JSON's order name can be just the id (the
    REST order lookup doesn't always return a name), and line items carry the
    same convention."""
    names = [(report.get("order") or {}).get("name") or ""]
    names += [li.get("name") or "" for li in report.get("line_items") or []]
    return next((n for n in names if len(n.split("_")) >= 14), names[0])


def _d(s: str):
    try:
        return date.fromisoformat(str(s)[:10])
    except ValueError:
        return None


def _fmt(d: date) -> str:
    return f"{d.day} {d:%b %Y}"


def derive(report: dict, ae_names: dict) -> dict:
    name = convention_name(report)
    tok = name.split("_")
    adv = _words(tok[7]) if len(tok) > 8 else (report.get("order") or {}).get("advertiser", "")
    camp = _words(tok[8]) if len(tok) > 8 else name
    seller_tok = tok[-1] if len(tok) >= 14 else ""
    seller = ae_names.get(seller_tok, seller_tok)
    lis = report.get("line_items") or []
    starts = [d for d in (_d(li.get("start")) for li in lis) if d]
    ends = [d for d in (_d(li.get("end")) for li in lis) if d]
    flight = f"{_fmt(min(starts))} – {_fmt(max(ends))}" if starts and ends else ""
    fmts = []
    for li in lis:
        f = dl.derive_format(None, li.get("name") or "")
        if f and f not in fmts:
            fmts.append(f)
    sizes = sorted({str(r.get("rendered_creative_size")) for r in report.get("by_creative") or []
                    if r.get("rendered_creative_size") and "x" in str(r.get("rendered_creative_size"))},
                   key=lambda s: [int(x) if x.isdigit() else 0 for x in s.split("x")])
    label = " · ".join(x for x in (", ".join(fmts) or "Display", ", ".join(sizes)) if x)
    interstitial = any(f == "Interstitial" for f in fmts)
    return {"advertiser": adv, "campaign": camp, "seller": seller, "flight": flight,
            "line_label": label, "ctr_target": 0.003 if interstitial else 0.001}


def main() -> int:
    src, out_dir = Path(sys.argv[1]), Path(sys.argv[2])
    report = json.loads(src.read_text())
    ae = json.loads((REPO / "settings.json").read_text()).get("ae_names", {})
    a = derive(report, ae)
    env = os.environ.get
    for k in ("advertiser", "campaign", "seller", "flight", "line_label"):
        if (env(f"FR_{k.upper()}") or "").strip():
            a[k] = env(f"FR_{k.upper()}").strip()
    if (env("FR_CTR_TARGET") or "").strip():
        a["ctr_target"] = float(env("FR_CTR_TARGET")) / 100
    truthy = lambda k: (env(k) or "").strip().lower() in ("1", "true", "yes")  # noqa: E731
    from build_final_report_xlsx import build  # noqa: E402
    out_dir.mkdir(parents=True, exist_ok=True)
    safe = "".join(c for c in f"{a['advertiser']} {a['campaign']}" if c not in '/\\:*?"<>|')
    out = out_dir / f"{safe} final report.xlsx"
    build(report, str(out), name=f"{a['advertiser']} — {a['campaign']}",
          advertiser=a["advertiser"], campaign=a["campaign"], seller=a["seller"],
          flight=a["flight"], line_label=a["line_label"], ctr_target=a["ctr_target"],
          logo=None, template=str(REPO / "templates" / "final_report_template.xlsx"),
          delivery_note=None, daily_note=None,
          omit_measurement=truthy("FR_OMIT_MEASUREMENT"), revenue=truthy("FR_REVENUE"))
    print(json.dumps(a, ensure_ascii=False))
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
