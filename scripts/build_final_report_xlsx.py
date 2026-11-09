#!/usr/bin/env python3
"""Build the client Excel final report for a Direct campaign.

The look is fixed by `templates/final_report_template.xlsx` — the Newsweek
"Paper" workbook first sent for the AppleTv Sky Program F3 report (Summary /
Daily delivery / Breakdown, Newsweek wordmark, Georgia + Franklin Gothic,
paper fills). This script loads that template and only rewrites the data and
wording, so every report looks the same. Never restyle it by hand; a report
that "doesn't look like the Sky one" is the failure this exists to prevent.

Input is the JSON that `scripts/final_campaign_report.py` writes (artifact
`final-report` of `.github/workflows/final_campaign_report.yml`).

Usage:
  python scripts/build_final_report_xlsx.py final_report.json out.xlsx \
      --name "American Hospital Dubai — Interview, AI Health Summit 2026" \
      --advertiser "American Hospital Dubai" \
      --campaign "Interview, AI Health Summit 2026" \
      --seller "Amit Shah" \
      --flight "14 Sep 2026 – 26 Oct 2026 (extended to 2 Nov 2026)" \
      --line-label "Display · 300x250, 970x250" \
      [--ctr-target 0.001] [--logo client_logo.png] \
      [--delivery-note "the extension delivered the final impressions on 2 Nov"] \
      [--daily-note "No impressions served 27 Oct – 1 Nov; ..."]

--logo takes the client's logo (PNG/JPG). Its white margins are trimmed and
its white background made transparent, and it is placed top right of the
Summary page next to the Newsweek wordmark.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import tempfile
import zipfile
from copy import copy
from datetime import date
from pathlib import Path

from openpyxl import load_workbook

REPO = Path(__file__).resolve().parent.parent
TEMPLATE = REPO / "templates" / "final_report_template.xlsx"


def _snap(ws, r, ncol):
    return [copy(ws.cell(row=r, column=c)._style) for c in range(1, ncol + 1)], \
        ws.row_dimensions[r].height


def _apply(ws, r, st):
    styles, h = st
    for c, s in enumerate(styles, 1):
        ws.cell(row=r, column=c)._style = copy(s)
    ws.row_dimensions[r].height = h


def _clear(ws, r0, r1, blank):
    for r in range(r0, r1 + 1):
        for c in range(1, len(blank[0]) + 1):
            ws.cell(row=r, column=c).value = None
        _apply(ws, r, blank)


def _day(k: str) -> str:
    d = date.fromisoformat(k)
    return f"{d.day} {d:%b %Y}"


def transparent_logo(src: str, dst: str) -> str:
    """Trim white margins and turn the white background transparent, keeping
    anti-aliased edges (alpha ramps over near-white, colour un-blended from
    white so there is no white fringe on the paper background)."""
    import numpy as np
    from PIL import Image, ImageChops

    im = Image.open(src).convert("RGB")
    diff = ImageChops.difference(im, Image.new("RGB", im.size, (255, 255, 255)))
    box = diff.convert("L").point(lambda p: 255 if p > 18 else 0).getbbox()
    if box:
        pad = 6
        im = im.crop((max(0, box[0] - pad), max(0, box[1] - pad),
                      min(im.width, box[2] + pad), min(im.height, box[3] + pad)))
    a_rgb = np.asarray(im).astype(float)
    alpha = np.clip((248 - a_rgb.min(axis=2)) / (248 - 190), 0, 1)
    safe = np.where(alpha > 0, alpha, 1)[..., None]
    rgb = np.clip((a_rgb - 255 * (1 - alpha[..., None])) / safe, 0, 255)
    Image.fromarray(np.dstack([rgb, alpha * 255]).astype(np.uint8), "RGBA").save(dst)
    return dst


def suppress_excel_flags(path: str) -> None:
    """Excel marks the template's totals (a ratio formula beside SUMs) as
    'Inconsistent Formula' and the GAM order id (text on purpose, so it never
    shows as 4.19E+09) as 'Number stored as text'. Both are intentional; this
    writes <ignoredErrors> into each sheet so no green triangles show.
    Run it last: LibreOffice drops the element if it rewrites the file."""
    tmp = tempfile.mktemp(suffix=".xlsx", dir=os.path.dirname(os.path.abspath(path)))
    with zipfile.ZipFile(path) as zin, zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if re.match(r"xl/worksheets/sheet\d+\.xml$", item.filename):
                xml = re.sub(r"<ignoredErrors>.*?</ignoredErrors>", "", data.decode("utf-8"), flags=re.S)
                dim = re.search(r'<dimension ref="([A-Z]+\d+(?::[A-Z]+\d+)?)"', xml)
                tag = (f'<ignoredErrors><ignoredError sqref="{dim.group(1) if dim else "A1:Z500"}" '
                       'formula="1" formulaRange="1" numberStoredAsText="1"/></ignoredErrors>')
                m = re.search(r"<(drawing|legacyDrawing|legacyDrawingHF|picture|oleObjects|controls|"
                              r"webPublishItems|tableParts|extLst)[\s>/]", xml)
                xml = xml[:m.start()] + tag + xml[m.start():] if m else xml.replace("</worksheet>", tag + "</worksheet>")
                data = xml.encode("utf-8")
            zout.writestr(item, data)
    shutil.move(tmp, path)


MEASUREMENT_NOTE = ("Viewability and attention are not reported for this campaign: the "
                    "interstitial renders outside the ad frame that Google Ad Manager Active View "
                    "and DoubleVerify measure, so both read near zero however the ad was seen.")


def _drop_measurement(wb, note=MEASUREMENT_NOTE) -> None:
    """Remove viewability + attention from every sheet, keeping the Sky look.

    For formats that render outside the GPT iframe (interstitial takeovers,
    breakouts) Active View and DV read ~0% however the ad was seen — Nocturne
    (2026-11) had 51 viewable of 2.5M with a 7.4% CTR. Roger's call: leave both
    out of the client version and say why in one line."""
    def paper(ws, cells, ref):
        st = copy(ws[ref]._style)
        for c in cells:
            cell = ws[c]
            if cell.__class__.__name__ != "MergedCell":
                cell.value = None
            cell._style = copy(st)

    def unmerge(ws, rng):
        if rng in {str(m) for m in ws.merged_cells.ranges}:
            ws.unmerge_cells(rng)

    s = wb["Summary"]
    # KPI tiles: keep Impressions / % of goal / Clicks / CTR (B-E)
    paper(s, [f"{c}{r}" for c in "FG" for r in (8, 9)], "A8")
    # highlights: delivery, CTR, device, then the measurement note
    s["B13"], s["B14"], s["B15"], s["B16"] = s["B15"].value, s["B16"].value, f"•  {note}", None
    # line-item table: drop VIEWABILITY / ATTENTION columns
    paper(s, [f"{c}{r}" for c in "HI" for r in (27, 28, 29)], "A27")
    # benchmarks: Delivery, CTR; drop viewability + attention rows
    for c in "BCDEF":
        s[f"{c}34"]._style = copy(s[f"{c}35"]._style)  # CTR's % format, not viewability's
        s[f"{c}34"] = s[f"{c}35"].value
    for r in (35, 36):
        unmerge(s, f"F{r}:I{r}")
        paper(s, [f"{c}{r}" for c in "BCDEFGHI"], "A35")
        s.row_dimensions[r].height = None
    s["C24"] = "GAM ad server"
    s["B38"] = ((s["B38"].value or "").replace("Delivery, clicks and viewability", "Delivery and clicks")
                .replace(" and Active View", ""))
    s["B39"] = note
    # About the metrics: CTR only
    s["B43"], s["C43"] = s["B45"].value, s["C45"].value
    s.row_dimensions[43].height = s.row_dimensions[45].height
    for r in (44, 45):
        unmerge(s, f"C{r}:I{r}")
        paper(s, [f"{c}{r}" for c in "BCDEFGHI"], "A44")
        s.row_dimensions[r].height = None

    w = wb["Daily delivery"]
    last = max(r for r in range(9, w.max_row + 1) if w[f"B{r}"].value == "Total")
    paper(w, [f"{c}{r}" for c in "FGHI" for r in range(8, last + 1)], "A8")
    w[f"B{last + 2}"] = note

    b = wb["Breakdown"]
    rows = [r for r in range(8, b.max_row + 1)
            if b[f"B{r}"].value == "SEGMENT" or (b[f"C{r}"].value is not None and r > 9)]
    paper(b, [f"{c}{r}" for c in "FGH" for r in rows], "A8")
    nrow = max(r for r in range(8, b.max_row + 1)
               if isinstance(b[f"B{r}"].value, str) and b[f"B{r}"].value.startswith("Attention is not"))
    b[f"B{nrow}"] = note


def build(d: dict, out: str, *, name, advertiser, campaign, seller, flight,
          line_label, ctr_target=0.001, logo=None, template=TEMPLATE,
          delivery_note=None, daily_note=None, omit_measurement=False) -> None:
    wb = load_workbook(template)
    by_day = sorted(d["by_day"], key=lambda x: x["date"])
    att = (d.get("dv_attention") or {}).get("daily") or {}
    att_all = (d.get("dv_attention") or {}).get("overall")
    tot = d["totals"]
    goal = tot["goal"]
    vw = tot["av_viewable"] / tot["av_measurable"] if tot["av_measurable"] else 0
    ctr = tot["clicks"] / tot["impressions"] if tot["impressions"] else 0
    full = [x for x in by_day if x["impressions"] >= 1000 and x["av_measurable"]]
    dvw = [100 * x["av_viewable"] / x["av_measurable"] for x in full] or [100 * vw]
    dev = sorted(d["by_device"], key=lambda x: -x["impressions"])
    top_dev = max(dev, key=lambda x: x["clicks"]) if dev else None
    order_id = str(d["order"]["id"])
    att_txt = f"{_day(min(att))} – {_day(max(att))}" if att else None
    ctr_t = f"{100 * ctr_target:.2f}%"

    # ── Summary ─────────────────────────────────────────────────────────
    s = wb["Summary"]
    s["B5"] = name
    s["B6"] = f"Flight {flight} · Order {order_id}"
    s["B12"] = (f"•  Delivered {100 * tot['impressions'] / goal:.1f}% of goal "
                f"({tot['impressions']:,} of {goal:,} impressions)"
                + (f"; {delivery_note}." if delivery_note else " by the flight's end."))
    s["B13"] = (f"•  Attention index {att_all:.0f}: {abs(att_all - 100):.0f}% "
                f"{'above' if att_all >= 100 else 'below'} DoubleVerify's 100 baseline "
                f"(daily {min(att.values()):.0f}–{max(att.values()):.0f})."
                if att_all is not None else "•  DoubleVerify attention was not reported for this line.")
    s["B14"] = (f"•  Viewability {100 * vw:.1f}%, {'above' if vw >= 0.7 else 'below'} the 70% Newsweek "
                f"target (daily {min(dvw):.1f}–{max(dvw):.1f}% on full delivery days).")
    s["B15"] = (f"•  CTR {100 * ctr:.2f}% ({tot['clicks']:,} clicks); "
                f"{'met' if ctr >= ctr_target else 'below'} the {ctr_t} Newsweek target.")
    s["B16"] = (f"•  {top_dev['device_category_name']} drove "
                f"{100 * top_dev['clicks'] / tot['clicks']:.0f}% of clicks at "
                f"{100 * top_dev['clicks'] / top_dev['impressions']:.2f}% CTR, the strongest device."
                if top_dev and tot["clicks"] and top_dev["impressions"] else None)
    s["C19"], s["C20"], s["C21"], s["C22"], s["C23"] = advertiser, campaign, flight, seller, order_id
    s["B28"] = line_label
    s["C28"], s["D28"], s["F28"] = goal, tot["impressions"], tot["clicks"]
    s["H28"] = s["H29"] = vw
    s["I28"] = s["I29"] = att_all
    s["C33"], s["C34"], s["C35"], s["C36"] = tot["impressions"] / goal, vw, ctr, att_all
    s["D35"] = ctr_target
    # within 0.5% of goal reads as full delivery (Sky: 24 short of 1,562,500 = "Delivered in full")
    s["F33"] = "Delivered in full" if tot["impressions"] >= goal * 0.995 else "Under-delivered"
    s["F34"] = f"{'Above' if vw >= 0.7 else 'Below'} the 70% Newsweek target"
    s["F35"] = f"{'Met' if ctr >= ctr_target else 'Below'} the {ctr_t} Newsweek target"
    s["F36"] = (f"{att_all - 100:+.0f}% vs DV's 100 baseline" if att_all is not None else "Not reported")
    s["B38"] = (f"Delivery, clicks and viewability: Google Ad Manager ad server and Active View, "
                f"{by_day[0]['date']} to {by_day[-1]['date']}.")
    s["B39"] = (f"Attention: DoubleVerify Authentic Attention index, 100 = DV baseline; covers {att_txt}."
                if att_txt else "Attention: not reported by DoubleVerify for this line.")
    s["B40"] = f"Pulled {date.today().isoformat()}."
    if att_all is not None:
        s["C43"] = re.sub(r"so \S+ means about \S+ (more|less) attention",
                          f"so {att_all:.0f} means about {abs(att_all - 100):.0f}% "
                          f"{'more' if att_all >= 100 else 'less'} attention", s["C43"].value)

    # ── Daily delivery ─────────────────────────────────────────────────
    w = wb["Daily delivery"]
    w["B5"], w["B6"] = name, f"Flight {flight}"
    DATA, TOTAL, BLANK, NOTE = (_snap(w, r, 10) for r in (10, 30, 31, 32))
    _clear(w, 9, 200, BLANK)
    r = 9
    for x in by_day:
        _apply(w, r, DATA)
        w[f"B{r}"], w[f"C{r}"], w[f"D{r}"] = x["date"], x["impressions"], x["clicks"]
        w[f"E{r}"] = f'=IF(C{r}>0,D{r}/C{r},"")'
        w[f"F{r}"], w[f"G{r}"] = x["av_viewable"], x["av_measurable"]
        w[f"H{r}"] = f'=IF(G{r}>0,F{r}/G{r},"")'
        w[f"I{r}"] = att.get(x["date"])
        r += 1
    _apply(w, r, TOTAL)
    w[f"B{r}"] = "Total"
    for c in "CDFG":
        w[f"{c}{r}"] = f"=SUM({c}9:{c}{r - 1})"
    w[f"E{r}"] = f'=IF(C{r}>0,D{r}/C{r},"")'
    w[f"H{r}"] = f'=IF(G{r}>0,F{r}/G{r},"")'
    w[f"I{r}"] = att_all
    _apply(w, r + 2, NOTE)
    missing = [x["date"] for x in by_day if x["date"] not in att]
    w[f"B{r + 2}"] = ((f"The total is DV's average over the days it has reported ({att_txt})."
                       if att else "DoubleVerify attention was not reported for this line.")
                      + (f" {daily_note}" if daily_note
                         else f" No DV reading for {', '.join(_day(m) for m in missing)}." if att and missing else ""))

    # ── Breakdown ──────────────────────────────────────────────────────
    b = wb["Breakdown"]
    b["B5"], b["B6"] = name, f"Flight {flight}"
    TITLE, HEAD, BDATA, BTOT, BBLANK, BNOTE = (_snap(b, r, 9) for r in (8, 9, 10, 11, 12, 22))
    head_vals = [b.cell(row=9, column=c).value for c in range(1, 10)]
    _clear(b, 8, 200, BBLANK)

    def section(r, title, rows, label):
        _apply(b, r, TITLE)
        b[f"B{r}"] = title
        r += 1
        _apply(b, r, HEAD)
        for c, v in enumerate(head_vals, 1):
            b.cell(row=r, column=c).value = v
        r += 1
        first = r
        for x in rows:
            _apply(b, r, BDATA)
            b[f"B{r}"], b[f"C{r}"], b[f"D{r}"] = label(x), x["impressions"], x["clicks"]
            b[f"E{r}"] = f'=IF(C{r}>0,D{r}/C{r},"")'
            b[f"F{r}"], b[f"H{r}"] = x["av_viewable"], x["av_measurable"]
            b[f"G{r}"] = f'=IF(H{r}>0,F{r}/H{r},"")'
            r += 1
        _apply(b, r, BTOT)
        b[f"B{r}"] = "Total"
        for c in "CDFH":
            b[f"{c}{r}"] = f"=SUM({c}{first}:{c}{r - 1})"
        b[f"E{r}"] = f'=IF(C{r}>0,D{r}/C{r},"")'
        b[f"G{r}"] = f'=IF(H{r}>0,F{r}/H{r},"")'
        return r + 2

    r = section(8, "By creative size", sorted(d["by_creative"], key=lambda x: -x["impressions"]),
                lambda x: x["rendered_creative_size"])
    r = section(r, "By device", dev, lambda x: x["device_category_name"])
    geo = sorted(d.get("by_country") or [], key=lambda x: -x["impressions"])
    if len(geo) > 1:  # single-country flights (like Sky, US-only) skip this section
        top, rest = geo[:15], geo[15:]
        if rest:
            top = top + [{"country_name": f"Other ({len(rest)} countries)",
                          **{k: sum(x[k] for x in rest)
                             for k in ("impressions", "clicks", "av_viewable", "av_measurable")}}]
        r = section(r, "By country", top, lambda x: x["country_name"])
    _apply(b, r - 1, BNOTE)
    b[f"B{r - 1}"] = ("Attention is not broken out by size, device or country: DoubleVerify's "
                      "attention report is split by date and line item only.")

    if omit_measurement:
        _drop_measurement(wb)

    # ── client logo, top right of the Summary header ──────────────────
    if logo:
        from openpyxl.drawing.image import Image as XLImage
        from PIL import Image as PILImage
        png = transparent_logo(logo, tempfile.mktemp(suffix=".png"))
        img = XLImage(png)
        w0, h0 = PILImage.open(png).size
        img.height, img.width = 60, round(w0 * 60 / h0)
        s.add_image(img, "I2")

    wb.calculation.fullCalcOnLoad = True  # Excel computes every formula on open
    wb.save(out)
    suppress_excel_flags(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("report_json")
    ap.add_argument("out")
    for a in ("--name", "--advertiser", "--campaign", "--seller", "--flight", "--line-label"):
        ap.add_argument(a, required=True)
    ap.add_argument("--ctr-target", type=float, default=0.001,
                    help="CTR target as a fraction (default 0.001 = 0.10%%, Newsweek display)")
    ap.add_argument("--logo", help="client logo PNG/JPG for the Summary page")
    ap.add_argument("--template", default=str(TEMPLATE))
    ap.add_argument("--delivery-note", help="replaces 'by the flight's end' in the delivery highlight "
                    "(e.g. 'the extension delivered the final impressions on 2 Nov')")
    ap.add_argument("--daily-note", help="sentence appended to the Daily delivery footnote")
    ap.add_argument("--omit-measurement", action="store_true",
                    help="leave viewability + attention out (formats that render outside the "
                         "measured ad frame, e.g. interstitials reading ~0%%); adds a note why")
    a = ap.parse_args()
    build(json.load(open(a.report_json)), a.out, name=a.name, advertiser=a.advertiser,
          campaign=a.campaign, seller=a.seller, flight=a.flight, line_label=a.line_label,
          ctr_target=a.ctr_target, logo=a.logo, template=a.template,
          delivery_note=a.delivery_note, daily_note=a.daily_note,
          omit_measurement=a.omit_measurement)
    print(f"wrote {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
