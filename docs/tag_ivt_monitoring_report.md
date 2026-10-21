# TAG IVT monitoring report: monthly update runbook

How to refresh the **Newsweek IVT Monitoring Report** deck, the evidence pack for
TAG Certified Against Fraud (CAF), and the two workbooks behind it. Do this **every
month**, about 3 days after the month closes so DV's data is complete
(Roger, 2026-10-21). Nothing in the dashboard or the sweep
produces these numbers. The GAM columns have a scripted pull (step 1). The
DV and GA4 numbers are copied by hand.

**Deadline and reviewers.** TAG's deadline falls close to the end of the year.
Michael (CTO) and Kelly Liu (SVP, Yield & AdOps) review the deck before it
goes to TAG (Roger, 2026-10-21).
Send it to **Katrina Eddy**, VP Tech Assurance, Alliance for Audited Media
(AAM, TAG's auditor): katrina.eddy@auditedmedia.com · O 203-447-2883 ·
M 203-675-5652.

| File | What it holds | Feeds |
|---|---|---|
| `DV-GA_discrepancy_<year>.xlsx` (sheet `Y<year>`) | Pre-bid monitoring: GA4 web pageviews vs DV pre-bid requests, by month | Deck slide 4 |
| `DV-GAM_discrepancy_report.xlsx` (sheets `inpage display <year>`, `instream video <year>`) | Post-bid monitoring: GAM impressions by demand channel, DV post-bid gross impressions, DV SIVT rate, by month | Deck slide 10 (SIVT), plus the DV↔GAM reconciliation |
| `NEWSWEEK_IVT_-_<year>_MONITORING_REPORT.pptx` | The deck | TAG auditor / buyers |

### What's in each workbook

**DV-GA discrepancy** (`DV-GA_discrepancy_<year>.xlsx`, one sheet `Y<year>`,
titled "Prebid - DV vs GA Page views"). **Pre-bid monitoring.** The goal is to show
TAG and TAG auditors that every pageview is monitored by DoubleVerify: comparing
GA4 pageviews with DV pre-bid requests shows how close DV's coverage is to 100%. One row per month (`202601`, …), grouped
Q1–Q4, with a Totals row at the bottom.

| Column | What it holds | Source |
|---|---|---|
| Month of Year | `YYYYMM` | — |
| Platform | always `web` | — |
| Pageviews | GA4 web views for the month | GA4 (step 3) |
| Prebid Reqs | pages DV's pre-bid integration evaluated | DV (step 3) |
| Discrepancy | `(Pageviews − Prebid Reqs) / Pageviews` | formula, don't overwrite |

**DV-GAM discrepancy** (`DV-GAM_discrepancy_report.xlsx`). **Post-bid
monitoring.** The goal is to show TAG and TAG auditors that every post-bid
impression is measured: comparing all impressions GAM records with what DV
records shows how close DV's post-bid coverage is to 100%. It reconciles what Ad
Manager counted against what DoubleVerify measured post-bid, and holds DV's
SIVT rate. Two sheets with the same layout: **`inpage display <year>`** (ad unit
`newsweek`) and **`instream video <year>`** (ad unit `vid.newsweek`). One row per
month, Totals row at the bottom.

| Column | What it holds | Source |
|---|---|---|
| Month and year | the month | — |
| Ad Exchange | Ad Exchange impressions (includes Private Auction) | GAM (step 1) |
| Open Bidding | Open Bidding impressions | GAM (step 1) |
| PMP – PA | Private Auction impressions | GAM (step 1) |
| Ad server | Ad server unfiltered downloaded impressions | GAM (step 1) |
| PMP – PD+PG | Preferred Deal + Programmatic Guaranteed impressions | GAM (step 1) |
| GAM total (header "GAM Total code served count" on display, "GAM gross impressions" on video) | display: Total code served count; video: formula Ad Exchange + PMP – PA + Ad server | GAM / formula |
| total DV gross impressions – post-bid | impressions DV measured post-bid | DV (step 2) |
| DV-GAM discrepancy | `1 − DV impressions / GAM total` | formula, don't overwrite |
| SIVT Rate | DV's sophisticated invalid traffic rate, as a decimal | DV (step 2) |

**Where the files live:** SharePoint, `yield › Documents › TAG › <year>`, one
folder per year (2018–2026). The workbooks and that year's reports sit in the
year folder; start a new folder each January. TAG folder:
<https://newsweek0.sharepoint.com/:f:/r/sites/yield/Shared%20Documents/TAG?d=w6f5bf7a5ebcb486fb227e5800637b2be&csf=1&web=1&e=ocTR9G>.

The order matters: **GAM → DV → GA4 → workbooks → deck.** Each step below says
which cells it fills.

**The workbooks are the reference** (Roger, 2026-10-20). Every definition below
is the one the 2026 workbooks already use, so each new month is comparable with
the months already in them:
- **Discrepancy** = `(GA4 pageviews − DV pre-bid) / GA4 pageviews`, the formula
  in the DV-GA sheet. Slide 4 uses the same formula.
- **GAM total** = Total code served count on the display sheet, and
  Ad Exchange + PMP – PA + Ad server on the video sheet (the sheet's formula).
- **Ad Exchange** comes from the inventory-format split: the Banner
  row for display; **In-stream video + In-stream video or audio** for video
  (always both — Roger, 2026-10-20).

---

## 1. GAM: the GAM columns, by dimension and metric

Each column is defined below by its GAM **dimensions, metric and filters**.
Build it in Ad Manager or pull it through the API; both read the same fields,
so the result is the same either way.

**Fast path:** dispatch **`.github/workflows/tag_ivt_gam_pull.yml`** (inputs
`start` / `end` as `YYYY-MM`; defaults run January through last closed month).
It runs `scripts/tag_ivt_gam_pull.py`, which queries the definitions below and
prints both sheets' GAM columns (Ad Exchange through GAM total) in workbook order to the run summary, with a
CSV attached. Locally:
`GAM_SERVICE_ACCOUNT_JSON=… GAM_NETWORK_ID=22541732127 python scripts/tag_ivt_gam_pull.py --start 2026-10 --end 2026-12`.
Checked against the 2026 workbook for Jan–Sep (2026-10-20): **99 of 108 cells
match exactly** (100 once display January is corrected). The rest are workbook
entry errors, listed below.

**Manual path:** Ad Manager → Reporting → Interactive reports → **Create my own
report** → Historical. For every column:

| Setting | GAM UI | API (`ReportDefinition`) |
|---|---|---|
| Date range | the months you need | `FixedDateRange` |
| Dimension | **Month and year** | `MONTH_YEAR` |
| Dimension | **Ad unit (top level)** | `AD_UNIT_NAME_TOP_LEVEL` |
| Filter | **Ad unit (top level)** is any of `newsweek`, `vid.newsweek` | `AD_UNIT_NAME_TOP_LEVEL IN (…)` |

The `newsweek` rows go to the display sheet and the `vid.newsweek` rows to the
video sheet. Use the *top-level* ad unit: the leaf **Ad unit**
(`AD_UNIT_NAME`) = `newsweek` is the bare parent unit, ~173 impressions a month.

Then add the metric and any extra filter for the column:

| Column | Metric (UI · API) | Extra filter (UI · API) |
|---|---|---|
| **Ad Exchange** | **Ad Exchange impressions** · `AD_EXCHANGE_IMPRESSIONS` | none. **Exception:** replace the Ad unit (top level) dimension with **Inventory format** · `INVENTORY_FORMAT_NAME`, and drop the ad-unit filter. Take the **Banner** row for display. For video, **add In-stream video + In-stream video or audio** (always both) |
| **Open Bidding** | **Total impressions** · `IMPRESSIONS` | **Demand channel** = Open Bidding · `DEMAND_CHANNEL_NAME` |
| **PMP – PA** | **Total impressions** · `IMPRESSIONS` | **Demand subchannel** = Private Auction · `DEMAND_SUBCHANNEL_NAME` |
| **Ad server** | **Ad server unfiltered downloaded impressions** · `AD_SERVER_UNFILTERED_DOWNLOADED_IMPRESSIONS` | none |
| **PMP – PD+PG** | **Total impressions** · `IMPRESSIONS` | **Demand subchannel** is any of Programmatic Guaranteed, Preferred Deals · `DEMAND_SUBCHANNEL_NAME` |
| **GAM total**, display | **Total code served count** · `CODE_SERVED_COUNT` | none (use the `newsweek` row) |
| **GAM total**, video | no metric: the sheet's formula, Ad Exchange + PMP – PA + Ad server | |

Settings for all of them: currency USD, time zone = publisher
(America/New_York). In the API, **Month and year** comes back as an integer,
`(year − 1900) × 12 + (month − 1)`; 1519 = 2026-08.

The workbook's **PMP – PD+PG** header row says "Ad server unfiltered downloaded
impressions", but the numbers in it are **Total impressions** (Jan 41,970,488;
unfiltered downloaded with the same filter is 42,209,988). Total impressions is
the convention (the script pulls it); rename the header to match.

The saved `TAG:` interactive reports in GAM (owner roger.hirano@newsweek.com)
produce these same numbers. The table above is the definition, so a column can
be rebuilt even if a saved report is changed, deleted or unshared.

### What the API check found

- **Ad Exchange impressions already include Private Auction.** Split Ad
  Exchange impressions by **Programmatic channel** (`PROGRAMMATIC_CHANNEL_NAME`)
  and Open Auction + Private Auction = the total, every month (Feb display:
  48,732,896 + 681,691 = 49,414,587). So Ad Exchange is never Programmatic channel
  = Open Auction, and PA is never added on top of Ad Exchange as if it were separate.
- **Display January, Ad Exchange: corrected to 59,956,722** (Roger, 2026-10-21 —
  "it's a mistake; full Ad Exchange for Jan too"). The original entry,
  59,562,722, was Ad Exchange filtered to **Programmatic channel = Open
  Auction**, i.e. missing January's 394,000 PA impressions. Every month uses the
  full Ad Exchange figure. Display GAM total isn't affected (it's code served
  count).
- **Video Ad Exchange, Jan and Jul–Sep: corrected** (Roger, 2026-10-21). The
  workbook had taken only the **In-stream video** row and left out **In-stream
  video or audio**. The rule is now both, always. Corrected values: Jan 3,236,963 (+1),
  Jul 1,332,567 (+83), Aug 1,716,850 (+82,199), Sep 2,370,358 (+42,531).
  Video GAM total moves by the same amounts, since it's
  Ad Exchange + PMP – PA + Ad server: Jan 46,725,759, Jul 28,890,985,
  Aug 26,572,383, Sep 32,903,406.
- **What the convention includes and leaves out** (by design, so months stay
  comparable):
  - Video GAM total (Ad Exchange + PMP – PA + Ad server) counts PA twice,
    because Ad Exchange already contains it. That's 0.03–0.4% of GAM total.
  - Video GAM total is ~70% of `vid.newsweek` code served count (Jan: 46.7M vs 65.4M).
    The display sheet uses code served count directly.

## 2. DoubleVerify: post-bid impressions and SIVT

From the **Inventory Quality report** in the DV UI (Pinnacle), separately for
display and video, by month. Both numbers come from that one report:

| Workbook column | DV number |
|---|---|
| **total DV gross impressions – post-bid** | Monitored (gross) impressions for the month, Newsweek O&O |
| **SIVT Rate** | Fraud/SIVT rate for the month (enter as a decimal: `0.0004` = 0.04%) |

**Applying the Media Type filter in the DV UI.** DV Pinnacle → Publisher Suite →
**Inventory Quality → Inventory**. Set the date picker to one month (e.g.
7/1/2026 – 7/31/2026), then **Add Filters → Media Type**, tick **Display** only
(leave Video and `null` unticked) and **Apply**. Read **Monitored Impressions**
(→ DV post-bid impressions) and **Fraud/SIVT Impressions ÷ Monitored
Impressions** (→ SIVT Rate; the same as 100% − the Fraud/SIVT Free Rate donut).
Repeat with **Video**, then the next month. Check: July 2026 Display reads
193.4M, the workbook's July display figure.

> **The SIVT goal is under 1%** (Roger, 2026-10-21). Worth knowing: for July
> 2026 (display + video, unfiltered) the UI shows 836.5K Fraud/SIVT of 218.9M
> monitored, about **0.38%** (Fraud/SIVT Free Rate 99.6%). The workbook's July
> SIVT Rate is `0.0004` (0.04%) display and `0.0001` (0.01%) video, roughly 10×
> lower. Both clear the 1% goal. Read the rate the same way every month so the
> trend stays comparable.

**Read the numbers off the UI tiles. No export is needed** (Roger, 2026-10-21).
A tile shows impressions to the nearest 100k (370.0M → 370,000,000), which is
how the 2026 column was filled. That rounding moves the DV↔GAM discrepancy by
at most ~0.03pp a month, well inside what TAG looks at.

**Cross-check against the cache.** The daily DV IVT email already lands in the
`dv_ivt` table (see `CLAUDE.md` → DoubleVerify IVT). Impression-weighted
SIVT% = Σ `monitored_ads` on Fraud/SIVT rows ÷ Σ `monitored_ads`, by month. That
table holds only the campaigns DV includes in the scheduled report, not the whole
site, so it is a sanity check on the UI figure and can't replace it. If the two
disagree by more than about 2×, find out why before you publish.

## 3. GA4 and DV pre-bid: the pageview comparison

| Workbook column (`Y<year>`) | Source |
|---|---|
| **Pageviews** | The saved GA4 exploration (below): **Year month** × **Platform**, metric **Views**, Platform = `web` |
| **Prebid Reqs** | DV pre-bid page-level requests for the month, from the monthly CSV DoubleVerify drops in its shared Google Drive folder (below) |
| **Discrepancy** | formula, already in the sheet |

**GA4 source of record:** this saved exploration (account 131632414, property
359317277):
<https://analytics.google.com/analytics/web/#/analysis/a131632414p359317277/edit/Gbsqfd36SraxUc8LJyQB6g>.
Open it, set the date range to the new month, and copy its `web` Views.
You need access to that property for the link to open, and the exploration
belongs to whoever saved it. If that person leaves, save a copy under the
shared team account first.

Fill one row per month (`202610`, `202611`, …). Leave the Totals row alone; it
is a `SUM`.

**DV pre-bid source:** DoubleVerify automated this and drops a new CSV every
month into a shared Google Drive folder:
<https://drive.google.com/drive/folders/1JXnbW3dBa-KjEO_W-aWQXCzUdApxmtxt>.
The folder is "TAG Certification [Client Shared Folder]" (shared with you, not in
My Drive). Each file is `report_YYYY-MM.csv` with one row per day and two
columns, `Day` and `Total`; the month's **Prebid Reqs** is the **sum of
`Total`**. Verified 2026-10-21: the sums for Apr–Sep 2026 match the workbook to
the request (e.g. Sep = 50,519,576).

## 4. Roll months into quarters

The deck reports quarters, and **SIVT has to be impression-weighted.** Don't
average the three monthly rates (MRC weighting, the same rule the dashboard
uses):

```
Quarter SIVT  = Σ(DV post-bid impressions × SIVT Rate) / Σ DV post-bid impressions
                over the quarter's 3 months
Overall SIVT  = the same, with display + video summed together
Quarter disc. = (Σ pageviews − Σ prebid) / Σ pageviews
```

Pageviews and prebid requests are counts, so a quarter is just the sum.

Each month, recompute the current quarter (quarter-to-date until its third
month is in) and the YTD row.

**2026 Q1–Q3 from the current workbooks** (so the next person can check their
arithmetic):

| | Pageviews | DV pre-bid | Discrepancy | SIVT overall | SIVT display | SIVT video |
|---|---|---|---|---|---|---|
| Q1 | 244,155,957 | 236,564,615 | 3.11% | 0.04% | 0.04% | 0.03% |
| Q2 | 124,297,981 | 119,051,343 | 4.22% | 0.04% | 0.05% | 0.02% |
| Q3 | 137,249,915 | 130,356,279 | 5.02% | 0.04% | 0.05% | 0.02% |
| YTD | 505,703,853 | 485,972,237 | 3.90% | 0.04% | 0.04% | 0.02% |

> **Slide 4 currently shows the wrong formula.** Its 3.21% / 4.41% / 3.61% are
> `(PV − DV) / DV`. Under the workbook's formula they should be **3.11% (Q1)**,
> **4.22% (Q2)** and **3.48% (H1)**, and the year row is now 3.90% YTD. Correct
> them on the next deck update.

## 5. Update the deck

| Slide | What to change |
|---|---|
| 1 | Title year. |
| 4 (GA4 vs DV pre-bid) | Add the new month's Pageviews / DV / Discrepancy, its quarter, and the **year row** (it is year-to-date, so it moves every month). Q4 placeholders read `0,0,0` / `0.0%` until filled. Check that each DV figure is inside its column group: Q2's DV number (119,051,343) sits in a loose text box outside the DV group and is easy to miss. Update the source footnote. |
| 10 (SIVT by quarter) | One row per quarter; columns Overall / Display / Video. The bottom row is labelled **Q4**. If it's meant to be the full-year figure, relabel it **Full year** (or YTD), so the Q4 row isn't mistaken for a real quarter. As of the 2026 Q2 copy, the Q3 (0.01%) and bottom-row values were not 2026 numbers. Replace them from the step 4 table. |
| 9 (Key changes) | Anything that changed traffic or measurement that month: replatforming, a new video player, a new verification vendor, a pre-bid threshold change. Q2 2026 pageviews fell by about half quarter over quarter, so the auditor will ask about it. |
| 5–7 | Coverage text (DV pre/post-bid, GAM filtration). Change only if the setup changed. **We no longer use IAS**: the 2025/2026 copies still have an IAS slide and name it among the "three pathways". Remove it so these slides name only DV and GAM (two pathways). |

Before sending it, check every percentage on slides 4 and 10 against the
workbook, and confirm the source footnotes name the year.

## Known gaps (fix when convenient)

- Slide 4's discrepancies use `÷ DV`; restate them `÷ pageviews` (step 4).
- Rename the PMP – PD+PG header to "Total impressions" to match its numbers (step 1).
- The DV pre-bid menu path isn't written down yet (step 3).
