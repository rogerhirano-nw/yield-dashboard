#!/usr/bin/env python3
"""
Parse the Magnite Demand Manager "Server Patterns" exports into the
Assertive-Yield migration reference doc (`ssp_params_migration.md`).

This keeps the markdown reproducible: drop refreshed exports into
`source_exports/` and re-run

    python docs/assertive_yield/parse_server_patterns.py

to regenerate the doc. The exports are wide sheets — row 0 carries the
`<bidder>:<channel>` group header (`puc` = display, `vast` = video), row 1 the
parameter name, and rows 2..14 the per-ad-unit values (col 0 = ad unit, col 2 =
sizes). Requires `openpyxl`.
"""
from __future__ import annotations

import json
import os
from collections import OrderedDict

import openpyxl

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "source_exports")
FILES = {
    "Desktop": os.path.join(SRC, "DemandManagerWeb_ServerNewsweek_Desktop_ServerPatterns.xlsx"),
    "Mobile": os.path.join(SRC, "DemandManagerWeb_ServerNewsweek_Mobile_ServerPatterns.xlsx"),
}
CHAN = {"puc": "Display (banner/native)", "vast": "Video (VAST)"}

# Magnite bidder key -> exchange / likely Prebid bidder code. Verify each
# against the Assertive Yield PBS build before porting.
NOTES = {
    "appnexus": "AppNexus / Xandr (Microsoft Monetize)",
    "triplelift": "TripleLift",
    "zeta_global_ssp": "Zeta Global SSP (via franklymedia reseller)",
    "medianet": "Media.net",
    "undertone": "Undertone",
    "rubicon": "Magnite / Rubicon Project",
    "onetag": "OneTag",
    "yahooAds": "Yahoo (yahooAds / yahoossp)",
    "openx": "OpenX",
    "sharethrough": "Sharethrough",
    "minutemedia": "Minute Media",
    "ix": "Index Exchange",
    "imds": "Advangelists / IMDS",
    "insticator": "Insticator",
    "nativo": "Nativo",
    "vidazoo": "Vidazoo",
    "pubmatic": "PubMatic",
    "ogury": "Ogury",
    "sovrn": "Sovrn",
    "kargo": "Kargo",
    "sparteo": "Sparteo",
    "oms": "OMS / Online Media Solutions",
    "openweb": "OpenWeb",
    "smartadserver": "Equativ / Smart AdServer",
    "amx": "AMX RTB",
    "inmobi": "InMobi",
    "seedtag": "Seedtag",
    "mobkoi": "Mobkoi",
}


def load(path):
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb["Patterns"]
    rows = list(ws.iter_rows(values_only=True))
    wb.close()
    ncols = max(len(r) for r in rows)
    grid = [[(rows[r][c] if c < len(rows[r]) else None) for c in range(ncols)] for r in range(len(rows))]

    def cell(r, c):
        v = grid[r][c]
        return "" if v is None else str(v).strip()

    adunits = OrderedDict((r, cell(r, 0)) for r in range(2, 15) if cell(r, 0))
    sizes = {r: cell(r, 2) for r in range(2, 15)}

    groups, cur = [], None
    for c in range(3, ncols):
        if cell(0, c):
            cur = {"header": cell(0, c), "cols": []}
            groups.append(cur)
        if cur is not None:
            cur["cols"].append(c)

    parsed = []
    for g in groups:
        bidder, _, channel = g["header"].partition(":")
        params = OrderedDict()
        for c in g["cols"]:
            pname = cell(1, c)
            if not pname:
                continue
            params[pname] = {r: cell(r, c) for r in adunits if cell(r, c)}
        parsed.append({"bidder": bidder, "channel": channel, "params": params})
    return {"adunits": adunits, "sizes": sizes, "groups": parsed}


def render(platform, data):
    out, adunits, sizes = [], data["adunits"], data["sizes"]
    out.append(f"### {platform} ad units\n")
    out.append("| Ad unit (`adUnit_device`) | Sizes (Media Type Object) |")
    out.append("|---|---|")
    for r, nm in adunits.items():
        out.append(f"| `{nm}` | {sizes.get(r, '')} |")
    out.append("")

    for chan in ["puc", "vast"]:
        groups = [g for g in data["groups"] if g["channel"] == chan]
        if not groups:
            continue
        chan_units = [r for r in adunits if (sizes.get(r) == "video") == (chan == "vast")]
        out.append(f"#### {platform} — {CHAN.get(chan, chan)} bidders\n")
        for g in groups:
            bidder, params = g["bidder"], g["params"]
            present = [r for r in adunits if any(r in v for v in params.values())]
            const, varying = OrderedDict(), OrderedDict()
            for pname, vals in params.items():
                distinct = {vals[r] for r in present if vals.get(r)}
                if not distinct:
                    continue
                if len(distinct) == 1 and (all(vals.get(r) for r in present) or len(present) == 1):
                    const[pname] = next(iter(distinct))
                else:
                    varying[pname] = vals

            out.append(f"<details><summary>`{bidder}`</summary>\n")
            if not present:
                out.append("_No parameters configured (placeholder columns only)._\n")
                out.append("</details>\n")
                continue
            if len(present) < len(chan_units):
                covered = ", ".join("`" + adunits[r] + "`" for r in present)
                out.append(f"⚠️ Partial coverage — configured on only: {covered}\n")
            if const:
                out.append("Constant parameters (same across all configured ad units):\n")
                for k, v in const.items():
                    out.append(f"- `{k}` = `{v}`")
                out.append("")
            if varying:
                hdr = ["Ad unit"] + list(varying.keys())
                out.append("| " + " | ".join(hdr) + " |")
                out.append("|" + "|".join(["---"] * len(hdr)) + "|")
                for r in present:
                    cells = [f"`{adunits[r]}`"]
                    for p in varying:
                        v = varying[p].get(r, "")
                        cells.append(f"`{v}`" if v else "—")
                    out.append("| " + " | ".join(cells) + " |")
                out.append("")
            out.append("</details>\n")
    return "\n".join(out)


def build_roster(datas):
    roster = OrderedDict()
    for plat, d in datas.items():
        for g in d["groups"]:
            has_vals = any(v for v in g["params"].values())
            e = roster.setdefault(g["bidder"], {k: False for k in
                                                ("Desktop-disp", "Desktop-vid", "Mobile-disp", "Mobile-vid")})
            key = f"{plat}-{'disp' if g['channel'] == 'puc' else 'vid'}"
            if has_vals:
                e[key] = True
    return roster


def main():
    datas = {plat: load(path) for plat, path in FILES.items()}
    roster = build_roster(datas)
    yn = lambda b: "✅" if b else "—"
    roster_rows = "\n".join(
        f"| `{b}` | {NOTES.get(b, '')} | {yn(e['Desktop-disp'])} | {yn(e['Desktop-vid'])} "
        f"| {yn(e['Mobile-disp'])} | {yn(e['Mobile-vid'])} |"
        for b, e in roster.items()
    )
    doc = DOC_TEMPLATE.format(
        n_bidders=len(roster),
        roster_tbl=roster_rows,
        desktop=render("Desktop", datas["Desktop"]),
        mobile=render("Mobile", datas["Mobile"]),
    )
    out_path = os.path.join(HERE, "ssp_params_migration.md")
    with open(out_path, "w") as f:
        f.write(doc)
    print(f"wrote {out_path} ({len(doc)} bytes, {len(roster)} bidders)")


DOC_TEMPLATE = open(os.path.join(HERE, "_doc_template.md")).read() if os.path.exists(
    os.path.join(HERE, "_doc_template.md")) else None

if __name__ == "__main__":
    if DOC_TEMPLATE is None:
        raise SystemExit("missing _doc_template.md next to this script")
    main()
