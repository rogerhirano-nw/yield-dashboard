# Campaign final reports (end-of-flight)

How to build the final report for a Direct campaign: a **doc** (Claude Docs)
and a **client Excel workbook**. Both follow the AppleTv Sky Program F3 report
(Oct 2026), which is the house format: **same layout, same look**. Roger's
words when a report drifted from it: "the look and feel is not the same". Don't
design a new one; fill the template.

## Order id in, doc + workbook out

1. **Pull.** Dispatch `.github/workflows/final_campaign_report.yml` with
   `order=<GAM order id>` (and `end_date` only to cut the window short). It
   runs `scripts/final_campaign_report.py` (read-only) and uploads the
   `final-report` artifact: `final_report.md` + `final_report.json`. Pushes to
   the dev branch also run it, on the order the workflow defaults to.
   - The window is **flight start → today (ET)**, not yesterday: final reports
     are asked for the day the last impressions serve (an extension that closes
     the goal the same day). The report says when it includes today; same-day
     GAM figures can still rise a little.
   - Totals by line item, plus by day / creative (rendered size) / device /
     country, all with Active View viewable + measurable counts.
   - **DoubleVerify attention comes from the DV report emails, not the
     `dv_attention` table** (see *Attention coverage* below).
2. **Workbook.** Download the artifact, then:
   ```bash
   python scripts/build_final_report_xlsx.py final_report.json "<Advertiser> <Campaign> final report.xlsx" \
     --name "<Advertiser> — <Campaign>" --advertiser "<Advertiser>" --campaign "<Campaign>" \
     --seller "<AE full name>" --flight "<d Mon yyyy – d Mon yyyy>" \
     --line-label "<Format · sizes>" --logo templates/logos/<client>.png
   ```
   It loads `templates/final_report_template.xlsx` (the Sky workbook with its
   data blanked) and only rewrites data and wording, so the look can't drift.
   `--delivery-note` / `--daily-note` add campaign-specific sentences (e.g. an
   extension). Excel computes the formulas on open; to check numbers before
   sending, recalculate a **copy** with LibreOffice (recalculating the original
   strips the Excel-flag suppression, see below).
3. **Doc.** A Claude Docs doc with the Sky doc's sections, in this order:
   title `<Advertiser> — <Campaign>: final report` · byline · lead sentence
   (delivered of goal, clicks + CTR, viewability, attention) · **Campaign**
   (a "Client workbook: … (Excel file)" line, the field table: GAM order, line
   item, IO/deal, flight, geo/format, agency/seller; then the KPI table:
   Delivered · Goal · % of goal · Clicks · CTR · Viewability · Attention) ·
   **Against Newsweek benchmarks** (paragraph + Metric / campaign / Target /
   Read table) · **Delivery breakdown** (paragraph + device table + note) ·
   **Daily delivery** (paragraph + table, **newest day first**, with an
   Attention column) · **About the metrics** (Attention index, Viewability,
   CTR) · source line. Tables, not charts: the Sky doc has none.
4. Add a row to *Instances*.

## Rules learned (American Hospital Dubai, 2026-11-02)

- **Targets:** viewability 70%; **CTR 0.10% for display** (Roger: "0.10% CTR
  is within Newsweek CTR"); the Sky interstitial report used 0.30%. Pass
  `--ctr-target` when a format's target differs. `settings.json`
  `benchmarks_by_format.Display.ctr_pct` still says 0.3, so the dashboard
  grades display CTR against 0.30% — reconcile it if Roger confirms.
- **Attention coverage.** `dv_attention` is TRUNCATE+replaced from the two
  newest DV emails on every sweep, so it only ever holds ~8 days. A flight that
  ended more than a week ago finds almost nothing there (AHD: 2 of 44 days, and
  a misleading 138 instead of the flight's 134). The report script now reads the
  DV emails covering the whole flight (newest email wins per date) and falls
  back to the table. If attention still covers only part of the flight, say so
  in the report and never present it as flight-wide.
- **Client logo** goes top right of the Summary page, next to the Newsweek
  wordmark, **on a transparent background** (`--logo` trims the margins and
  removes the white). Keep finished logos in `templates/logos/`. If the
  client's site won't serve the file (AHD's dxh.ae had an expired certificate),
  ask for the file; a pasted image does not reach the build, only an attachment.
- **No Excel warning triangles.** The totals (a ratio beside SUMs) trip
  "Inconsistent Formula" and the GAM order id (text on purpose) trips "Number
  stored as text". The builder writes `<ignoredErrors>` into every sheet as its
  last step.
- **Revenue is left out** when the line's rate in GAM is $0 (AHD was).
- **`[nw]` advertisers** (AHD runs under `[nw] Investment-Reports`) are
  excluded from the dashboard's Direct table, so the report has to come from
  GAM, not the dashboard cache.
- The **2 Nov extension day** had 10 impressions and no DV reading yet; daily
  ranges in the highlights use full delivery days (≥1,000 impressions) so a
  10-impression day doesn't set the floor.

## Instances

| Campaign | Order | Doc | Workbook | Notes |
| --- | --- | --- | --- | --- |
| AppleTv Sky Program — FY26 Flight3 | 4203010941 | [Doc](https://claude.ai/artifact/2ADoKs48n13sG5SBnSkqeQ) | `AppleTV_Sky_Program_F3_final_report.xlsx` | The template; interstitial, CTR target 0.30% |
| American Hospital Dubai — Interview, AI Health Summit 2026 | 4194246183 | [Doc](https://claude.ai/artifact/SJtPVsg3b19VchHAtZAocS) | `American Hospital Dubai AI Health Summit final report.xlsx` | 1,000,007 / 1,000,000; extended to 2 Nov; attention 134 over 43 days; CTR 0.10% vs 0.10% |
