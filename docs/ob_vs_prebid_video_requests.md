# OB vs Prebid Server video ad requests — why the bars aren't comparable

**2026-09-18.** Magnite raised the Open Bidding video ad-request volume "in
comparison to PB server": their Seller-Integration-Type chart shows Open Bidding
at **265.8M** ad requests against **128.2M** for Prebid Server (RP Hosted), a
2.07x gap, on 2026-08-18 → 2026-09-16 video traffic.

**The gap is a counting boundary, not a volume difference.** Only 37.5% of those
OB ad requests become an auction; 98.2% of Prebid Server's do. On auctions —
the first column where both sides are measuring the same event — Prebid Server
runs **more** than OB.

## The window totals (Magnite's own report, Video, 30 days)

| Integration | Ad Requests | Auctions | Auc/AR | Paid Impr | Revenue | eCPM | $/1k AR | $/1k auctions |
|---|---|---|---|---|---|---|---|---|
| Open Bidding | 265,819,907 | 99,800,117 | **37.5%** | 4,658,480 | $39,333 | $8.44 | $0.148 | **$0.394** |
| Prebid Server (RP Hosted) | 128,241,828 | 125,920,498 | **98.2%** | 2,398,427 | $31,026 | $12.94 | $0.242 | $0.246 |
| A9 | 74,752,321 | 70,500,921 | 94.3% | 890,206 | $9,562 | $10.74 | $0.128 | $0.136 |
| Prebid Server (3p Hosted) | 64,499,359 | 45,259,579 | 70.2% | 36,971 | $551 | $14.90 | $0.009 | $0.012 |
| Exchange API | 59,893,880 | 58,005,470 | 96.8% | 433,166 | $5,504 | $12.71 | $0.092 | $0.095 |
| **Total** | **593,207,295** | **399,486,585** | 67.3% | 8,417,250 | **$85,975** | $10.21 | $0.145 | — |

Source workbook: Magnite `RP_September-17th-2026-332-PM_UTC-0400`, dimensions
Seller Integration Type x Ad Format (Video) x Date.

## Why 37.5% is a definition, not a symptom

The OB auction rate lands between **33.4% and 42.1% on every one of the 30
days**. Every other integration sits at 92–99% (Prebid Server 3p Hosted is the
one noisy exception, 45.7–86.3% and drifting down through September). A ratio
that tight over a month is a boundary in the counting, not behaviour: Magnite
logs an OB ad request at the **Google callout**, before any auction decision,
while a Prebid Server request is counted at the auction itself. 166.0M of the
265.8M OB "ad requests" (62.5%) never reach an auction because they were never
going to be one.

**Cross-check path:** GAM's `YIELD_GROUP_CALLOUTS` *is* the ad-request count for
an OB yield partner, and the `video` yield group (id 680331) is 100% Open
Bidding, so a callout pull filtered to it is directly comparable with no
Mediation contamination. `scripts/pull_magnite_ob_video_requests.py` (+ the
matching one-off workflow) runs that reconciliation over the identical window
and prints the difference against the 265,819,907 claim.

## Two findings that outrank the question asked

**1. The chart's implied conclusion is backwards.** OB carries the *lowest*
eCPM in the table ($8.44 vs $12.94) but the *highest* revenue per auction —
$0.394, 60% above Prebid Server RP-hosted — because its auction-to-impression
fill is 4.67% against Prebid Server's 1.90%. OB is 44.8% of video ad requests
and 45.7% of video revenue. Per-ad-request yield ($0.148) reads badly only
because the denominator is inflated by the callouts above.

**2. Prebid Server (3p Hosted) is the real problem in this data.** 64.5M ad
requests (10.9% of video) and 45.3M auctions produced **36,971 paid impressions
and $551** across 30 days — 0.08% of its auctions filled, 0.6% of video revenue.
Its eCPM is the *highest* in the table ($14.90), so the few impressions it
clears are valuable; it simply isn't clearing them. That is a bigger dollar
question than the OB/Prebid Server comparison and should be raised separately.

## Don't use the Ad Responses column

It isn't per-auction. Open Bidding shows responses at 115.6% of auctions, and
Exchange API logs *more responses than requests* on several days (8/27:
2,796,203 responses on 2,066,843 requests). It appears to count bid responses
across bidders. Any argument built on it will not survive review.
