# TAG IVT monitoring report: quarterly update runbook

How to refresh the **Newsweek IVT Monitoring Report** deck, the evidence pack for
TAG Certified Against Fraud (CAF), and the two workbooks behind it. Do this once
a quarter, after the quarter closes. It is a manual pull: nothing in the
dashboard or the sweep produces these numbers.

| File | What it holds | Feeds |
|---|---|---|
| `DV-GA_discrepancy_<year>.xlsx` (sheet `Y<year>`) | GA4 web pageviews vs DV pre-bid requests, by month | Deck slide 4 |
| `DV-GAM_discrepancy_report.xlsx` (sheets `inpage display <year>`, `instream video <year>`) | GAM impressions by demand channel, DV post-bid gross impressions, DV SIVT rate, by month | Deck slide 10 (SIVT), plus the DV↔GAM reconciliation |
| `NEWSWEEK_IVT_-_<year>_MONITORING_REPORT.pptx` | The deck | TAG auditor / buyers |

The order matters: **GAM → DV → GA4 → workbooks → deck.** Each step below says
which cells it fills.

---

## 1. GAM: re-run the `TAG:` reports

Ad Manager → Reporting → Interactive reports, search **`TAG`**. All eleven are
owned by roger.hirano@newsweek.com, date range **This year**. Re-run each one
(open → Run), switch the date dimension to **Month and year**, and copy the
months you need.

**Which report fills which column.** The display sheet is filtered to ad unit
**`newsweek`** and the video sheet to **`vid.newsweek`**. Both are *top-level*
ad units, so filter on top-level ad unit: the leaf `AD_UNIT_NAME = newsweek` is
the bare parent unit, ~173 impressions a month (see the GAM facts in
`CLAUDE.md`).

| Workbook column | `inpage display` (ad unit `newsweek`) | `instream video` (ad unit `vid.newsweek`) | GAM metric |
|---|---|---|---|
| **B** Ad Exchange | `TAG: Ad Exchange Impressions` | `TAG: Ad Exchange Impressions` (video ad unit) | Ad Exchange impressions |
| **C** Open Bidding | `TAG: Open Bidding` | `TAG: Open Bidding` (video ad unit) | Yield group impressions |
| **D** PMP – PA | `TAG: Private Auction Display` | `TAG: Private Auction Video` | Total impressions |
| **E** Ad server | `TAG: Ad server unfiltered downloaded impressions` | same report, video ad unit | Ad server unfiltered downloaded impressions |
| **F** PMP – PD+PG | `TAG: Programmatic PD + PG - Display only` | `TAG: Programmatic PD + PG - Video only` | Ad server unfiltered downloaded impressions |
| **G** GAM total | `TAG: Total code served count`, **typed in** | **formula** `=B+D+E` | Total code served count |

Leave these three alone. Each shows a ⚠ last-run status in the list:
- `TAG: Total code served count Display` (last run 2026-08-21)
- `TAG: Open Exchange` (2026-07-28)
- `TAG: Preferred Deals and PGs` (2026-03-05)

They look like older versions of the reports above (PD+PG has since been split
into Display-only and Video-only). If one of them turns out to be the source of
record, update this table.

> **The two sheets define "GAM total" differently.** On display, G is a pasted
> *Total code served count*. Jan 2026 reads 401,193,896, and that matches no
> sum of B–F (B+C+D+E+F = 423,797,952). On video, G is `=B+D+E`: Ad Exchange +
> PA + ad server, leaving out Open Bidding (C) and PD+PG (F). Either can be
> defended, but the deck should not show two definitions as if they were one.
> Pick one and fix the other sheet. Code served count is the closer match to
> what DV measures post-bid, since DV fires when the creative is served.

## 2. DoubleVerify: post-bid impressions and SIVT

From the DV Pinnacle UI, separately for display and video, by month:

| Workbook cell | DV number |
|---|---|
| **H** total DV gross impressions – post-bid | Monitored (gross) impressions for the month, Newsweek O&O |
| **J** SIVT rate | Fraud/SIVT rate for the month (enter as a decimal: `0.0004` = 0.04%) |

**Export the exact count. Don't read it off a UI tile.** The 2026 H column is
rounded to the nearest 100k (370,000,000 / 316,400,000 / …), which is what a
headline tile shows. That rounding moves the DV↔GAM discrepancy by up to ~0.03pp
a month. Use the Pinnacle report export (Standard report → Impressions +
Fraud/SIVT by month, display and video as separate rows).

**Cross-check against the cache.** The daily DV IVT email already lands in the
`dv_ivt` table (see `CLAUDE.md` → DoubleVerify IVT). Impression-weighted
SIVT% = Σ `monitored_ads` on Fraud/SIVT rows ÷ Σ `monitored_ads`, by month. That
table holds only the campaigns DV includes in the scheduled report, not the whole
site, so it is a sanity check on the UI figure and can't replace it. If the two
disagree by more than about 2×, find out why before you publish.

## 3. GA4 and DV pre-bid: the pageview comparison

| Workbook column (`Y<year>`) | Source |
|---|---|
| **D** Pageviews | GA4 → Explore, dimensions **Year month** × **Platform**, metric **Views**, filter Platform = `web` |
| **E** Prebid Reqs | DV pre-bid page-level requests for the month (the pre-bid integration evaluates every page view before ad requests go out; see slide 7) |
| **F** Discrepancy | formula, already in the sheet |

Fill one row per month (`202610`, `202611`, …). Leave the Totals row alone; it
is a `SUM`.

> TODO, confirm and record here: the exact Pinnacle report and menu path for
> pre-bid requests. Whoever runs Q4 should write it down so this step stops
> depending on memory.

## 4. Roll months into quarters

The deck reports quarters, and **SIVT has to be impression-weighted.** Don't
average the three monthly rates (MRC weighting, the same rule the dashboard
uses):

```
Quarter SIVT  = Σ(H × J) / Σ H              over the quarter's 3 months
Overall SIVT  = Σ(H × J) over display + video  /  Σ H over display + video
Quarter disc. = (Σ pageviews − Σ prebid) / Σ pageviews
```

Pageviews and prebid requests are counts, so a quarter is just the sum.

**2026 Q1–Q3 from the current workbooks** (so the next person can check their
arithmetic):

| | Pageviews | DV pre-bid | Disc. (÷ PV) | Disc. (÷ DV) | SIVT overall | SIVT display | SIVT video |
|---|---|---|---|---|---|---|---|
| Q1 | 244,155,957 | 236,564,615 | 3.11% | 3.21% | 0.04% | 0.04% | 0.03% |
| Q2 | 124,297,981 | 119,051,343 | 4.22% | 4.41% | 0.04% | 0.05% | 0.02% |
| Q3 | 137,249,915 | 130,356,279 | 5.02% | 5.29% | 0.04% | 0.05% | 0.02% |
| YTD | 505,703,853 | 485,972,237 | 3.90% | 4.06% | 0.04% | 0.04% | 0.02% |

> **The deck and the workbook divide by different denominators.** The workbook
> computes `(PV − DV) / PV` (share of pageviews DV didn't see). Slide 4's
> 3.21% / 4.41% / 3.61% are `(PV − DV) / DV`. Pick one, and say which in the
> slide footnote. `÷ PV` matches the workbook and reads more naturally
> ("DV evaluated 96% of pageviews").

## 5. Update the deck

| Slide | What to change |
|---|---|
| 1 | Title year. |
| 4 (GA4 vs DV pre-bid) | Add the new quarter's Pageviews / DV / Discrepancy, and update the **year row** (it is year-to-date, so it moves every quarter). Q4 placeholders read `0,0,0` / `0.0%` until filled. Check that each DV figure is inside its column group: Q2's DV number (119,051,343) sits in a loose text box outside the DV group and is easy to miss. Update the source footnote. |
| 10 (SIVT by quarter) | One row per quarter; columns Overall / Display / Video. The bottom row is labelled **Q4**. If it's meant to be the full-year figure, relabel it **Full year** (or YTD), so the Q4 row isn't mistaken for a real quarter. As of the 2026 Q2 copy, the Q3 (0.01%) and bottom-row values were not 2026 numbers. Replace them from the step 4 table. |
| 9 (Key changes) | Anything that changed traffic or measurement that quarter: replatforming, a new video player, a new verification vendor, a pre-bid threshold change. Q2 2026 pageviews fell by about half quarter over quarter, so the auditor will ask about it. |
| 5–7 | Coverage text (DV pre/post-bid, IAS, GAM filtration). Change only if the setup changed. |

Before sending it, check every percentage on slides 4 and 10 against the
workbook, and confirm the source footnotes name the year.

## Known gaps (fix when convenient)

- DV post-bid impressions are rounded (step 2).
- The two sheets define GAM total differently (step 1).
- The deck and the workbook use different discrepancy denominators (step 4).
- The DV pre-bid menu path isn't written down yet (step 3).
- The GAM pulls could be scripted the way the other one-off pulls are (a
  workflow on `secrets.GAM_SERVICE_ACCOUNT_JSON`, like
  `pull_index_ob_requests.yml`) so the GAM half of the workbook fills itself.
  Not built.
