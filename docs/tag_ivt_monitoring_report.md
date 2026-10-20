# TAG IVT monitoring report: quarterly update runbook

How to refresh the **Newsweek IVT Monitoring Report** deck, the evidence pack for
TAG Certified Against Fraud (CAF), and the two workbooks behind it. Do this once
a quarter, after the quarter closes. Nothing in the dashboard or the sweep
produces these numbers. The GAM columns have a scripted pull (step 1). The
DV and GA4 numbers are copied by hand.

| File | What it holds | Feeds |
|---|---|---|
| `DV-GA_discrepancy_<year>.xlsx` (sheet `Y<year>`) | GA4 web pageviews vs DV pre-bid requests, by month | Deck slide 4 |
| `DV-GAM_discrepancy_report.xlsx` (sheets `inpage display <year>`, `instream video <year>`) | GAM impressions by demand channel, DV post-bid gross impressions, DV SIVT rate, by month | Deck slide 10 (SIVT), plus the DV↔GAM reconciliation |
| `NEWSWEEK_IVT_-_<year>_MONITORING_REPORT.pptx` | The deck | TAG auditor / buyers |

The order matters: **GAM → DV → GA4 → workbooks → deck.** Each step below says
which cells it fills.

---

## 1. GAM: the `TAG:` reports (or one scripted pull)

**Fast path:** dispatch **`.github/workflows/tag_ivt_gam_pull.yml`** (inputs
`start` / `end` as `YYYY-MM`; defaults run January through last closed month).
It runs `scripts/tag_ivt_gam_pull.py`, prints both sheets' columns B–G in
workbook order to the run summary, and uploads a CSV. Locally:
`GAM_SERVICE_ACCOUNT_JSON=… GAM_NETWORK_ID=22541732127 python scripts/tag_ivt_gam_pull.py --start 2026-10 --end 2026-12`.

The script rebuilds the reports through the API rather than opening them. The
saved UI reports are private to their owner, so the service account gets a 404
on them by id. Checked against the 2026 workbook for Jan–Sep (2026-10-20): it
reproduces display C–G and video C–F exactly. The column B differences are
explained below.

**Manual path:** Ad Manager → Reporting → Interactive reports, search
**`TAG`**. All eleven are owned by roger.hirano@newsweek.com, with date range
**This year** and split **Totals only**. Re-run each one and copy the months
you need. The definitions below come from the reports themselves (2026-10-20).
"AU" means *Ad unit (top level)*. Use the top-level unit: the leaf
`AD_UNIT_NAME = newsweek` is the bare parent unit, ~173 impressions a month.

| Col | Sheet | UI report (id) | Dimensions | Metric | Filters |
|---|---|---|---|---|---|
| **B** | both | `TAG: Ad Exchange Impressions` (6778957922) | Inventory format × Month | Ad Exchange impressions | none. **Banner** row → display, **In-stream video** row → video |
| **C** | both | `TAG: Open Bidding` (6778924131) | Month × Demand channel × AU | Total impressions | Demand channel = Open Bidding; AU ∈ newsweek, vid.newsweek |
| **D** | display | `TAG: Private Auction Display` (6779703054) | Month × AU × Inventory format | Total impressions | Demand subchannel = Private Auction; AU = newsweek |
| **D** | video | `TAG: Private Auction Video` (6778990880) | Month × AU | Total impressions | Demand subchannel = Private Auction; AU = vid.newsweek |
| **E** | both | `TAG: Ad server unfiltered downloaded impressions` (6837002391) | AU × Month | Ad server unfiltered downloaded impressions | AU ∈ newsweek, vid.newsweek |
| **F** | display | `TAG: Programmatic PD + PG - Display only` (6779748229) | Month × AU | **Total impressions** (the report also carries unfiltered downloaded; the workbook uses Total) | Demand subchannel ∈ Programmatic Guaranteed, Preferred Deals; AU = newsweek |
| **F** | video | `TAG: Programmatic PD + PG - Video only` (6779754895) | Month × AU | Total impressions | Demand subchannel ∈ Programmatic Guaranteed, Preferred Deals; AU = vid.newsweek |
| **G** | display | `TAG: Total code served count` (6837114072) | AU × Month | Total code served count | AU ∈ newsweek, vid.newsweek (use the `newsweek` rows) |
| **G** | video | none: **formula** `=B+D+E` | | | |

The workbook's column F header says "Ad server unfiltered downloaded
impressions", but the numbers in it are **Total impressions** (Jan 41,970,488;
the unfiltered figure is 42,209,988). Fix the header or the source so the two
agree.

**Superseded. Don't use these three** (each shows ⚠ in the list):
- `TAG: Total code served count Display` (6779042369): the same query as
  6837114072, filtered to `newsweek` only.
- `TAG: Open Exchange` (6779681686): Programmatic channel × Month × AU, Ad
  Exchange impressions, Demand channel = Ad Exchange. Its *Open Auction* rows
  are Ad Exchange **minus** Private Auction.
- `TAG: Preferred Deals and PGs` (6779516327): Month × Ad server impressions,
  Programmatic channel ∈ PD/PG, Inventory format ≠ In-stream video. It returns
  the same numbers as the Display-only report, which replaced it.

### What the API check found

- **Ad Exchange impressions already include Private Auction.** For each month,
  Open Auction + PA = Ad Exchange exactly (Feb display: 48,732,896 + 681,691 =
  49,414,587). Two consequences:
  - **Display January, column B** reads 59,562,722, the Open Auction figure from
    the superseded `TAG: Open Exchange` report. February–September are full Ad
    Exchange. January is short by exactly 394,000, which is January's PA. Use
    59,956,722.
  - **The video sheet's `G = B+D+E` counts PA twice**: once inside B, and again
    as D.
- **Video column B misses part of Ad Exchange video.** GAM splits Ad Exchange
  video on `vid.newsweek` into two inventory formats, *In-stream video* and
  *In-stream video or audio*. The UI report splits by format, and only the
  first row was copied. The gap was 82,199 in Aug (5%) and 42,531 in Sep. The
  script filters on the ad unit, so it counts both.
- **The two sheets define GAM total differently.** On display, G is code
  served count (Jan 401,193,896). On video, G is `B+D+E` (Jan 46.7M), while
  code served for `vid.newsweek` that month is 65.4M. Against DV's 40.9M, that
  is a 12% discrepancy one way and a 37% discrepancy the other. Pick one
  definition and use it on both sheets. The script prints both video figures
  so you can compare.

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
| **D** Pageviews | The saved GA4 exploration (below): **Year month** × **Platform**, metric **Views**, Platform = `web` |
| **E** Prebid Reqs | DV pre-bid page-level requests for the month (the pre-bid integration evaluates every page view before ad requests go out; see slide 7) |
| **F** Discrepancy | formula, already in the sheet |

**GA4 source of record:** this saved exploration (account 131632414, property
359317277):
<https://analytics.google.com/analytics/web/#/analysis/a131632414p359317277/edit/Gbsqfd36SraxUc8LJyQB6g>.
Open it, set the date range to the quarter, and copy the `web` Views per month.
You need access to that property for the link to open, and the exploration
belongs to whoever saved it. If that person leaves, save a copy under the
shared team account first.

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
- The two sheets define GAM total differently, and the video formula counts PA
  twice (step 1).
- Display January column B and video column B are entered from the wrong report rows (step 1).
- The deck and the workbook use different discrepancy denominators (step 4).
- The DV pre-bid menu path isn't written down yet (step 3).
