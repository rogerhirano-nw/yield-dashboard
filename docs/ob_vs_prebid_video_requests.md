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

**An earlier draft of this doc claimed the bid rate corroborated it — that was
circular and has been withdrawn.** It divided *Magnite's* response count by
*GAM's* callout count to get "2.22 bids per callout" and called the closeness to
GAM's own 2.29 an independent agreement. It is the same denominator on both
sides, so it only ever restated the assumption. The genuine agreement is on the
two **numerators** (responses↔bids, impressions↔impressions); the ten-partner
callout spread above is what carries the denominator, and it does so on its own.

**The honest counter-argument, stated in full.** On Magnite's denominator its
bid rate is 115.4M / 265.8M = **43.4%**, which is an unremarkable SSP bid rate.
On GAM's it is 118.9M / 52.0M = **229%**, which requires Magnite to return
multiple bids per callout. And Magnite is the **only** video OB partner whose
bids exceed its callouts — the other nine run 0.00x to 0.26x:

| Partner | video callouts | video bids | bids/callout |
|---|---|---|---|
| **Magnite** | 52,036,623 | 118,929,248 | **2.29** |
| Media.net | 51,706,699 | 13,341,176 | 0.26 |
| PubMatic | 51,852,880 | 10,570,292 | 0.20 |
| OpenX | 51,624,004 | 9,593,299 | 0.19 |
| Equativ | 47,314,038 | 7,268,925 | 0.15 |
| TripleLift | 51,643,354 | 7,261,430 | 0.14 |
| Index Exchange | 52,081,338 | 6,032,525 | 0.12 |
| YieldMo / InMobi / Sharethrough | ~50M each | ≤287k | ≤0.01 |

On **display** Magnite is unremarkable (118.7M bids on 255.6M callouts, 0.46) —
the anomaly is video-only. Multi-seat bidding (a bid per deal/seat, which Magnite
does expose and the others may not) explains it; so would GAM under-counting
video callouts for this one partner. **This is unresolved**, and it is the single
strongest argument against the conclusion below, so it belongs in any
conversation with Magnite rather than being left out of one.

What it does *not* do is rescue the 265.8M. The ~52.0M opportunity count is
corroborated ten independent ways; GAM ran ~52.0M video auctions in the window,
full stop. Magnite cannot have received 265.8M **Open Bidding** requests out of
~52.0M auctions unless each auction sent it roughly five.

## On-page forensics: INCONCLUSIVE, and why (read before repeating it)

An earlier revision of this doc concluded from these probes that "the video ad
call is not made in the browser — it is served server-side by the player
vendor." **That was wrong and is withdrawn.** It inferred a mechanism from an
absence of requests in a session where the video could never have played.

`scripts/video_slot_forensics.py` against
`newsweek.com/texas-republicans-face-generational-wipeout-…`, 240s:

| What was checked | Result |
|---|---|
| `#nw-video-player` present | yes |
| `<video>` `paused` | `false` — **misleading, see below** |
| `<video>` `currentTime` after 240s | **0** (never advanced) |
| `<video>` `readyState` / `networkState` | **0 / 0** — no source ever attached |
| GAM `/gampad/ads` requests | 3, all `output=ldjh` (display), **no VAST** |
| `imasdk.googleapis.com/js/sdkloader/ima3.js` | **loaded** |
| `google.ima` / `google.ima.AdsLoader` | **both present** |
| `prebid.videostep.com/Bid/VideoAdContent` | 1 request, t=2.7s |

**The video never played.** `paused: false` only means `play()` was called; with
`readyState` and `networkState` both 0 and `currentTime` frozen at 0, no media
was ever loaded. Root cause, confirmed directly:

```
video.canPlayType('video/mp4; codecs="avc1.42E01E")  -> ''   (H.264: no)
video.canPlayType('audio/mp4; codecs="mp4a.40.2"')   -> ''   (AAC:   no)
video.canPlayType('application/vnd.apple.mpegurl')   -> ''   (HLS:   no)
video.canPlayType('video/webm; codecs="vp8, vorbis"') -> 'probably'
```

Playwright's bundled Chromium ships **without proprietary codecs**. The site's
video is H.264, so the player can never start, no ad break ever occurs, and no
VAST request is ever made. The absence of video ad requests is an artifact of
the test environment, not a fact about the page.

**What the probe does establish**, and it points the opposite way from the
withdrawn claim: **the client-side video ad path exists.** The IMA SDK is loaded
and `google.ima.AdsLoader` is instantiated. When a real browser plays the video,
IMA requests VAST from `securepubads.g.doubleclick.net/gampad/ads` — which *is* a
GAM video ad request and *is* counted in the 52.0M callouts. So the
end-of-video re-request almost certainly does reach GAM as its own callout,
which **reinforces** the point that it inflates both sides equally and cannot
explain the 5.11x.

**To actually observe the sequential re-request** you need a browser with
proprietary codecs: `BROWSER_CHANNEL=chrome` against a real Chrome install
(the same escape hatch `scripts/prebid_render_forensics.py` documents for
SmileWanted), run from a laptop. A datacenter headless Chromium cannot do it.

## Third-party corroboration of the request mix (AssertiveYield)

AY's `prebid_analytics` for the identical window, `mediaType` x bidder:

| rubicon (client-side Prebid) | requests |
|---|---|
| banner | 1,365,007 |
| video | 230,055 |
| **video share** | **14.4%** |

GAM's own split is **16.9%** video (52.0M of 307.6M callouts). Two independent
systems put video at roughly a seventh of request volume. Magnite's video-only
claim of 265.8M is **86% of GAM's entire OB callout volume across both formats**
(307.6M) — for that to be an opportunity count, video would have to dominate the
mix, and neither GAM nor AY says it does.

**Caveat, load-bearing:** AY's prebid absolute counts run ~0.5% of GAM's callout
volume, so that dataset is sampled or narrowly scoped. **Only the ratios within
it are usable** — do not quote AY's raw request numbers against GAM's or
Magnite's. The sampling rate has not been calibrated.

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
