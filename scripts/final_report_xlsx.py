"""Excel rendering of a final report, skinned to the Newsweek "Paper" design
system (docs/design_handoff/newsweek-dashboard.css): warm-paper canvas, ink
text, serif display figures, Franklin Gothic UI, tracked-uppercase eyebrows.

Brand red is chrome only (the eyebrow tick); nothing here is severity-coloured.
Rates are live formulas over the counts beside them, so the sheet stays
consistent if a count is edited. Attention is DV's number and is typed in.
IVT is deliberately absent: not client-relevant (Roger, 2026-10-08).
"""
from __future__ import annotations

from pathlib import Path

from openpyxl import Workbook
from openpyxl.drawing.image import Image as XLImage
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

# --- tokens (mirror docs/design_handoff/newsweek-dashboard.css, light) ---
SURFACE_0 = "FEFCF6"   # --surface-0, page canvas
SURFACE_1 = "FFFFFF"   # --surface-1, tiles
SURFACE_2 = "F6F2E6"   # --surface-2, table header band
INK = "1F1E19"         # --text-primary / --border-strong
SECONDARY = "57564F"   # --text-secondary
MUTED = "8C887B"       # --text-muted
BORDER = "E7E0C9"      # --border (hairline)
BRAND_RED = "E91D0C"   # --brand-red, chrome only

SERIF = "Georgia"                  # Benton Modern Display fallback
SANS = "Franklin Gothic Book"      # Franklin Gothic; Excel substitutes if absent

CANVAS = PatternFill("solid", fgColor=SURFACE_0)
TILE = PatternFill("solid", fgColor=SURFACE_1)
BAND = PatternFill("solid", fgColor=SURFACE_2)
HAIR = Side(style="thin", color=BORDER)
STRONG = Side(style="medium", color=INK)
TICK = Side(style="thick", color=BRAND_RED)

F_EYEBROW = Font(name=SANS, size=8, bold=True, color=SECONDARY)
F_TITLE = Font(name=SERIF, size=20, color=INK)
F_SUB = Font(name=SANS, size=10, color=SECONDARY)
F_SECTION = Font(name=SERIF, size=13, color=INK)
F_HEAD = Font(name=SANS, size=8, bold=True, color=SECONDARY)
F_BODY = Font(name=SANS, size=10, color=INK)
F_BODY_B = Font(name=SANS, size=10, bold=True, color=INK)
F_NOTE = Font(name=SANS, size=8, italic=True, color=MUTED)
F_KPI_L = Font(name=SANS, size=8, bold=True, color=SECONDARY)
F_KPI_V = Font(name=SERIF, size=18, color=INK)

INT = '#,##0;-#,##0;"–"'
PCT1 = '0.0%;-0.0%;"–"'
PCT2 = '0.00%;-0.00%;"–"'
ATT = '0;-0;"–"'


def _canvas(ws, rows: int, cols: int) -> None:
    for r in range(1, rows + 1):
        for c in range(1, cols + 1):
            ws.cell(r, c).fill = CANVAS
    ws.sheet_view.showGridLines = False


NEWSWEEK_LOGO = Path(__file__).resolve().parent.parent / "assets" / "newsweek_logo.png"


def _logo(path, height_px: int) -> XLImage | None:
    if not path or not Path(path).exists():
        return None
    img = XLImage(str(path))
    img.width, img.height = round(img.width * height_px / img.height), height_px
    return img


def _masthead(ws, eyebrow: str, title: str, sub: str,
              client_logo=None, right_col: str = "I") -> int:
    """Logo row (Newsweek wordmark left, client mark right), then the
    eyebrow / serif title / subtitle stack."""
    ws.row_dimensions[2].height = 36
    nw = _logo(NEWSWEEK_LOGO, 26)
    if nw:
        ws.add_image(nw, "B2")
    cl = _logo(client_logo, 50)
    if cl:
        ws.add_image(cl, f"{right_col}2")
    e = ws.cell(4, 2, eyebrow.upper())
    e.font = F_EYEBROW
    e.border = Border(left=TICK)
    e.alignment = Alignment(indent=1)
    ws.cell(5, 2, title).font = F_TITLE
    ws.row_dimensions[5].height = 30
    ws.cell(6, 2, sub).font = F_SUB
    return 8


def _section(ws, row: int, text: str) -> int:
    ws.cell(row, 2, text).font = F_SECTION
    ws.row_dimensions[row].height = 20
    return row + 1


def _table(ws, row: int, headers: list[str], rows: list[list], fmts: list[str | None],
           total: list | None = None, widths_left: bool = True) -> int:
    """Header band + hairline rows (+ an optional bold total row). Cells whose
    value is a str starting with '=' are formulas; `{r}` is the row number."""
    for j, h in enumerate(headers):
        c = ws.cell(row, 2 + j, h.upper())
        c.font, c.fill = F_HEAD, BAND
        c.border = Border(bottom=STRONG)
        c.alignment = Alignment(horizontal="left" if j == 0 else "right",
                                vertical="center", wrap_text=True)
    ws.row_dimensions[row].height = 24
    first = row + 1
    for i, vals in enumerate(rows):
        r = first + i
        for j, v in enumerate(vals):
            if isinstance(v, str) and "{r}" in v:
                v = v.replace("{r}", str(r))
            c = ws.cell(r, 2 + j, v)
            c.font, c.fill = F_BODY, TILE
            c.border = Border(bottom=HAIR)
            c.alignment = Alignment(horizontal="left" if j == 0 else "right",
                                    vertical="center")
            if fmts[j]:
                c.number_format = fmts[j]
    last = first + len(rows) - 1
    if total:
        r = last + 1
        for j, v in enumerate(total):
            if isinstance(v, str):
                v = v.replace("{r}", str(r)).replace("{first}", str(first)).replace("{last}", str(last))
            c = ws.cell(r, 2 + j, v)
            c.font, c.fill = F_BODY_B, TILE
            c.border = Border(top=STRONG, bottom=HAIR)
            c.alignment = Alignment(horizontal="left" if j == 0 else "right")
            if fmts[j]:
                c.number_format = fmts[j]
        last = r
    return last + 2


def build_xlsx(p: dict, path: str, client_logo: str | None = None) -> None:
    o, t = p["order"], p["totals"]
    dv = p.get("dv") or {}
    att_all = dv.get("attention_overall")
    att_by_li = {str(a["line_item_id"]): a.get("attention_index") for a in dv.get("attention") or []}
    att_day = dv.get("attention_daily") or {}
    advertiser = o.get("display_advertiser") or o.get("advertiser") or ""
    campaign = o.get("display_campaign") or o["name"]

    wb = Workbook()

    # ------------------------------------------------------------ Summary
    ws = wb.active
    ws.title = "Summary"
    _canvas(ws, 60, 12)
    ws.column_dimensions["A"].width = 3
    for col, w in zip("BCDEFGHIJK", (30, 17, 17, 17, 17, 17, 17, 14, 14, 3)):
        ws.column_dimensions[col].width = w
    row = _masthead(ws, "Newsweek · Campaign final report",
                    f"{advertiser} — {campaign}" if advertiser else campaign,
                    f"Flight {p['flight_label']} · Order {o['id']}"
                    + (f" · {o['po_number']}" if o.get("po_number") else ""),
                    client_logo, "I")

    # KPI strip: label row, value row (tiles). Values are formulas into the
    # line-item table / DV cells below so the strip can't disagree with them.
    kpis = ["Impressions", "% of goal", "Clicks", "CTR", "Viewability", "Attention"]
    lr, vr = row, row + 1
    for j, k in enumerate(kpis):
        lc = ws.cell(lr, 2 + j, k.upper())
        lc.font, lc.fill = F_KPI_L, TILE
        lc.border = Border(top=STRONG if j else TICK, left=HAIR, right=HAIR)
        lc.alignment = Alignment(horizontal="left", indent=1)
        vc = ws.cell(vr, 2 + j)
        vc.font, vc.fill = F_KPI_V, TILE
        vc.border = Border(left=HAIR, right=HAIR, bottom=HAIR)
        vc.alignment = Alignment(horizontal="left", indent=1, vertical="center")
    ws.row_dimensions[vr].height = 34
    row = vr + 2

    # Campaign details
    row = _section(ws, row, "Campaign")
    details = [
        ["Advertiser", advertiser or "—"],
        ["Campaign", campaign],
        ["Flight", p["flight_label"]],
        ["Seller", o.get("seller") or "—"],
        ["GAM order", f"{o['id']}" + (f" ({o['po_number']})" if o.get("po_number") else "")],
        ["Measurement", "GAM ad server + Active View; DoubleVerify Attention"],
    ]
    for k, v in details:
        a = ws.cell(row, 2, k.upper())
        a.font, a.fill, a.border = F_HEAD, TILE, Border(bottom=HAIR)
        b = ws.cell(row, 3, v)
        b.font, b.fill, b.border = F_BODY, TILE, Border(bottom=HAIR)
        ws.merge_cells(start_row=row, start_column=3, end_row=row, end_column=9)
        row += 1
    row += 1

    # Line items
    row = _section(ws, row, "Delivery by line item")
    li_rows = []
    by_li = {r["line_item_id"]: r for r in p["by_line_item"]}
    for li in p["line_items"]:
        r = by_li.get(li["id"], {})
        goal = li["goal_units"] if li.get("goal_units") and li["goal_units"] > 0 else None
        li_rows.append([
            li.get("display_name") or li["name"],
            goal,
            r.get("ad_server_impressions", 0),
            "=IF(C{r}>0,D{r}/C{r},\"\")",
            r.get("ad_server_clicks", 0),
            "=IF(D{r}>0,F{r}/D{r},\"\")",
            r.get("ad_server_active_view_viewable_impressions", 0) /
            r["ad_server_active_view_measurable_impressions"]
            if r.get("ad_server_active_view_measurable_impressions") else None,
            att_by_li.get(li["id"]),
        ])
    li_first = row + 1
    row = _table(ws, row,
                 ["Line item", "Goal", "Delivered", "% of goal", "Clicks", "CTR", "Viewability", "Attention"],
                 li_rows, [None, INT, INT, PCT1, INT, PCT2, PCT1, ATT],
                 total=["Total", "=SUM(C{first}:C{last})", "=SUM(D{first}:D{last})",
                        "=IF(C{r}>0,D{r}/C{r},\"\")", "=SUM(F{first}:F{last})",
                        "=IF(D{r}>0,F{r}/D{r},\"\")",
                        t["viewability_pct"] / 100 if t.get("viewability_pct") is not None else None,
                        att_all])
    li_total = row - 2
    for rr in range(li_first, li_total + 1):
        ws.cell(rr, 2).alignment = Alignment(wrap_text=True, vertical="center")

    notes = [
        "Delivery, clicks and viewability: Google Ad Manager ad server and Active View, "
        f"{p['window']['start']} to {p['window']['end']}.",
        "Attention: DoubleVerify Authentic Attention index, 100 = DV baseline"
        + (f"; covers {dv['attention_window']}." if dv.get("attention_window") else "."),
        f"Pulled {p['pulled']}.",
    ]
    for n in notes:
        ws.cell(row, 2, n).font = F_NOTE
        row += 1

    # KPI values now that the referenced cells exist
    vals = [f"=D{li_total}", f"=E{li_total}", f"=F{li_total}", f"=G{li_total}",
            f"=H{li_total}", f"=I{li_total}"]
    for j, (v, fmt) in enumerate(zip(vals, (INT, PCT1, INT, PCT2, PCT1, ATT))):
        c = ws.cell(vr, 2 + j, v)
        c.number_format = fmt

    # ------------------------------------------------------------ Daily
    wd = wb.create_sheet("Daily delivery")
    n = len(p.get("by_day") or [])
    _canvas(wd, n + 12, 11)
    wd.column_dimensions["A"].width = 3
    for col, w in zip("BCDEFGHIJ", (14, 14, 12, 10, 14, 14, 12, 12, 3)):
        wd.column_dimensions[col].width = w
    r0 = _masthead(wd, "Newsweek · Daily delivery", f"{advertiser} — {campaign}" if advertiser else campaign,
                   f"Flight {p['flight_label']}", client_logo, "I")
    drows = []
    for d in p.get("by_day") or []:
        drows.append([
            d["date"],
            d["ad_server_impressions"],
            d["ad_server_clicks"],
            "=IF(C{r}>0,D{r}/C{r},\"\")",
            d["ad_server_active_view_viewable_impressions"],
            d["ad_server_active_view_measurable_impressions"],
            "=IF(G{r}>0,F{r}/G{r},\"\")",
            att_day.get(str(d["date"])[:10]),
        ])
    end = _table(wd, r0, ["Date", "Impressions", "Clicks", "CTR", "Viewable impr.",
                          "Measurable impr.", "Viewability", "Attention"],
                 drows, [None, INT, INT, PCT2, INT, INT, PCT1, ATT],
                 total=["Total", "=SUM(C{first}:C{last})", "=SUM(D{first}:D{last})",
                        "=IF(C{r}>0,D{r}/C{r},\"\")", "=SUM(F{first}:F{last})",
                        "=SUM(G{first}:G{last})", "=IF(G{r}>0,F{r}/G{r},\"\")", att_all])
    wd.cell(end, 2, "Attention is blank on days DoubleVerify has not reported; "
                    "the total is DV's flight average.").font = F_NOTE
    wd.freeze_panes = wd.cell(r0 + 1, 3)

    # ------------------------------------------------------------ Breakdown
    wb_ = wb.create_sheet("Breakdown")
    _canvas(wb_, 40, 9)
    wb_.column_dimensions["A"].width = 3
    for col, w in zip("BCDEFGH", (22, 14, 12, 10, 14, 14, 3)):
        wb_.column_dimensions[col].width = w
    r = _masthead(wb_, "Newsweek · Delivery breakdown", f"{advertiser} — {campaign}" if advertiser else campaign,
                  f"Flight {p['flight_label']}", client_logo, "H")
    for key, title, col in (("by_size", "By creative size", "rendered_creative_size"),
                            ("by_device", "By device", "device_category_name")):
        rows = p.get(key) or []
        if not rows:
            continue
        r = _section(wb_, r, title)
        brows = [[x[col], x["ad_server_impressions"], x["ad_server_clicks"],
                  "=IF(C{r}>0,D{r}/C{r},\"\")",
                  x["ad_server_active_view_viewable_impressions"],
                  "=IF(H{r}>0,F{r}/H{r},\"\")",
                  x["ad_server_active_view_measurable_impressions"]] for x in rows]
        r = _table(wb_, r, ["Segment", "Impressions", "Clicks", "CTR", "Viewable impr.",
                            "Viewability", "Measurable impr."],
                   brows, [None, INT, INT, PCT2, INT, PCT1, INT],
                   total=["Total", "=SUM(C{first}:C{last})", "=SUM(D{first}:D{last})",
                          "=IF(C{r}>0,D{r}/C{r},\"\")", "=SUM(F{first}:F{last})",
                          "=IF(H{r}>0,F{r}/H{r},\"\")", "=SUM(H{first}:H{last})"])
    wb_.column_dimensions["H"].width = 14

    for s in wb.worksheets:
        s.sheet_properties.tabColor = INK
        s.page_setup.orientation = "landscape"
        s.page_setup.fitToWidth = 1
        s.sheet_properties.pageSetUpPr.fitToPage = True
    wb.save(path)
