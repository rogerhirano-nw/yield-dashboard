# OB vs Prebid Server video ad requests — the gap is in Magnite's request column

**2026-09-18.** Magnite raised the Open Bidding video ad-request volume "in
comparison to PB server": their Seller-Integration-Type chart shows Open Bidding
at **265.8M** ad requests against **128.2M** for Prebid Server (RP Hosted) on
2026-08-18 → 2026-09-16 video traffic, a 2.07x gap.

**GAM only sent Magnite 52.0M video requests in that window.** Google is the
side that *sends* an Open Bidding callout, so its count settles what Magnite
received. Magnite's ad-request column is **5.11x** what Google actually sent —
and once that is corrected, Prebid Server carries **2.46x more** video request
volume than Open Bidding, the exact opposite of the chart.

## The reconciliation

GAM `YIELD_GROUP_CALLOUTS` / `BIDS` / `AUCTIONS_WON` / `IMPRESSIONS`, buyer
"Magnite fka Rubicon Project", `video` yield group, identical window
([run 35376751276](https://github.com/rogerhirano-nw/yield-dashboard/actions/runs/35376751276)):

| Metric | Magnite | GAM | Ratio | Delta |
|---|---|---|---|---|
| Ad requests / callouts | 265,819,907 | 52,036,623 | **5.11x** | +410.8% |
| Auctions / auctions won | 99,800,117 | 36,487,791 | 2.74x | +173.5% |
| Ad responses / bids | 115,402,553 | 118,929,248 | **0.97x** | −3.0% |
| Paid impressions / impressions | 4,658,480 | 4,762,385 | **0.98x** | −2.2% |

**The bottom of the funnel reconciles and the top does not.** Responses agree
within 3.0% and impressions within 2.2% — that is two independent systems
describing the same traffic, which is what makes the request-column gap a
finding rather than a mismatch of scope. Whatever Magnite is counting as an "ad
request," it resolves down to exactly the bids and impressions GAM sees.

**GAM's callout count is the opportunity count.** Open Bidding calls out to
every yield partner on every opportunity, and the report bears that out: across
the ten OB buyers in the `video` group, callouts run 47,314,038 to 52,081,338 —
a 9.2% spread. The video yield group ran ~52.0M auctions in the window. It is
not possible for Magnite alone to have received 265.8M of them.

**Magnite's own bid rate corroborates it.** GAM logs 118.9M bids on 52.0M
callouts = **2.29 bids per callout**; Magnite logs 115.4M responses = **2.22 per
callout**. Both sides agree Magnite returns just over two bids per opportunity.
Against Magnite's own 265.8M request figure that would be 0.43 responses per
request, which is not what either system shows.

## What it means for the comparison

Rebuilt on the request counts each side actually receives — GAM's callouts for
OB, Magnite's own figure for Prebid Server (which has no equivalent cross-check,
but whose 98.2% auction rate implies it is already counting opportunities):

| | Open Bidding | Prebid Server (RP) | |
|---|---|---|---|
| Video requests | 52,036,623 | 128,241,828 | PBS **2.46x** |
| Paid impressions | 4,658,480 | 2,398,427 | OB **1.94x** |
| Fill (impr / request) | **8.95%** | 1.87% | OB **4.8x** |
| Revenue | $39,333 | $31,026 | OB **1.27x** |
| Revenue / 1k requests | **$0.756** | $0.242 | OB **3.1x** |

Open Bidding is the *smaller* video request channel and the *better* one on
every outcome measure. The chart's implied reading — that OB is consuming
outsized request volume — is backwards on both halves.

## The one legitimate explanation to put to Magnite

**Video ad pods.** If a single OB callout carries several impression objects,
Magnite could correctly count several ad requests against one Google callout.
5.11 is a pod-shaped number. Worth asking directly before treating the column as
an error — it is the difference between a reporting artifact and a definition we
should simply account for. The same question covers the 2.74x on auctions.

Either way the column is not comparable with a Prebid Server request count
one-for-one, which is the operative point.

## Two findings that outrank the question asked

**1. Prebid Server (3p Hosted) is the real problem in this data.** 64.5M ad
requests (10.9% of video) and 45.3M auctions produced **36,971 paid impressions
and $551** across 30 days — 0.08% of its auctions filled, 0.6% of video revenue,
on the table's *highest* eCPM ($14.90). It is also the only integration whose
auction rate is unstable (45.7–86.3%, drifting down through September). Bigger
dollar question than the OB/Prebid Server comparison; raise separately.

**2. Open Bidding earns its share.** OB is 45.7% of video revenue on the lowest
eCPM in the table ($8.44 vs Prebid Server's $12.94), because it converts far
more of what it is given. Low eCPM on its own is not an efficiency argument.

## Magnite's Video window totals, as reported

| Integration | Ad Requests | Auctions | Auc/AR | Paid Impr | Revenue | eCPM | $/1k auctions |
|---|---|---|---|---|---|---|---|
| Open Bidding | 265,819,907 | 99,800,117 | 37.5% | 4,658,480 | $39,333 | $8.44 | $0.394 |
| Prebid Server (RP Hosted) | 128,241,828 | 125,920,498 | 98.2% | 2,398,427 | $31,026 | $12.94 | $0.246 |
| A9 | 74,752,321 | 70,500,921 | 94.3% | 890,206 | $9,562 | $10.74 | $0.136 |
| Prebid Server (3p Hosted) | 64,499,359 | 45,259,579 | 70.2% | 36,971 | $551 | $14.90 | $0.012 |
| Exchange API | 59,893,880 | 58,005,470 | 96.8% | 433,166 | $5,504 | $12.71 | $0.095 |
| **Total** | **593,207,295** | **399,486,585** | 67.3% | 8,417,250 | **$85,975** | $10.21 | — |

Source: Magnite `RP_September-17th-2026-332-PM_UTC-0400`, Seller Integration
Type × Ad Format (Video) × Date, 150 rows = 5 integrations × 30 days.

OB's auction rate sits in a **33.4–42.1%** band on every one of the 30 days
while every other integration is 92–99%. Given the reconciliation above, that
stability is the inflated request denominator showing through, not a delivery
problem — the ~5.1x fan-out is constant, so the ratio it produces is too.

## Don't use the Ad Responses column for anything else

It isn't per-auction. Open Bidding shows responses at 115.6% of *its own*
auctions, and Exchange API logs more responses than requests on several days
(8/27: 2,796,203 responses on 2,066,843 requests). It counts bid responses
across seats — which is exactly why it reconciles against GAM's `BIDS` and not
against anything else.

## Re-running

`scripts/pull_magnite_ob_video_requests.py` + `.github/workflows/pull_magnite_ob_video_requests.yml`.
The script prints every OB buyer by yield group first (the near-identical
callout counts are the proof that callouts are the opportunity count), then the
four-way reconciliation table, then the daily series. Change `START` / `END` to
move the window.
