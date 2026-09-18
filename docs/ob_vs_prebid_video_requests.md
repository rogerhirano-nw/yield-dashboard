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

## The refresh mechanism, read from the page's own code

Playback is impossible in this environment, but the ad stack can be read without
it. `window` on an article carries a purpose-built video-ad layer — Mux Player
(`mux-player-react` 3.13.0) plus a custom IMA integration
(`__imaIntegrationInitialized`, `__imaVastLoadTimeoutPatched`,
`__imaGetAdsManagerPatched`, `__videoAdStackReady: true`) — and two globals whose
own console messages are tagged **`[VIDEO REFRESH]`**:

- **`prebidVideoAd_refresh()`** → `window.pbjs.requestBids({ adUnitCodes: ["video"] })`,
  a **fresh client-side Prebid auction** for the `video` ad unit, whose winning
  targeting is flattened to `key=value&key=value` in `window.prebid_video_bid`.
- **`amznVideoAPS_refresh()`** → fetches **APS (Amazon) targeting** into
  `window.amzn_video_bid`, joined with `%26` — double-encoded because it is
  destined for the `cust_params` of a GAM ad tag.

So one video ad break runs: Prebid video auction + APS fetch → their targeting is
appended to the IMA ad tag → **IMA requests VAST from
`securepubads.g.doubleclick.net/gampad/ads`**. That last step is a GAM video ad
request, which is a **callout to every OB partner including Magnite**, and is
counted in `YIELD_GROUP_CALLOUTS`.

**This closes the question Roger's detail opened.** The end-of-video re-request is
real and it is client-side, so **every refresh increments GAM's callout count** —
the 52,036,623 already contains all of them. It also fires a fresh Prebid auction,
so the Prebid Server leg gets a request per refresh too. The refresh is
**symmetric across both integrations** and therefore cannot produce a gap between
them. Magnite's 5.11x remains unexplained by anything observable on the page.

(Confirmed read-only. `prebidVideoAd_refresh` and `amznVideoAPS_refresh` were
never invoked — calling them would fire real ad requests against production,
which is the exact metric in dispute.)

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

## Is the anomaly unique to Magnite? Yes — and it is sharper than first stated

Two vantage points cover every SSP. **GAM's own ledger** (`YIELD_GROUP_CALLOUTS`
/ `YIELD_GROUP_BIDS` per OB buyer, both yield groups) and **AssertiveYield**, a
neutral third party measuring the client-side Prebid auction.

**GAM side — bids per callout, every OB partner** (emitted directly by
`scripts/pull_magnite_ob_video_requests.py`, not transcribed):

| Partner | display b/c | **video b/c** | video bids | display bids | video/display bids |
|---|---|---|---|---|---|
| **Magnite** | 0.465 | **2.29** | 118,929,248 | 118,722,170 | **1.002** |
| Media.net | 0.334 | 0.26 | 13,341,176 | 84,685,661 | 0.158 |
| PubMatic | 0.346 | 0.20 | 10,570,292 | 87,862,180 | 0.120 |
| OpenX | 0.267 | 0.19 | 9,593,299 | 67,532,417 | 0.142 |
| Equativ | 0.265 | 0.15 | 7,268,925 | 61,990,136 | 0.117 |
| TripleLift | 0.097 | 0.14 | 7,261,430 | 24,602,205 | 0.295 |
| Index Exchange | 0.013 | 0.12 | 6,032,525 | 3,406,283 | 1.771 |
| YieldMo | 0.005 | 0.01 | 286,519 | 1,270,103 | 0.226 |
| InMobi OB | 0.008 | 0.00 | 35,685 | 1,920,299 | 0.019 |
| Sharethrough | 0.000 | 0.00 | 916 | 49,130 | 0.019 |

Magnite is the **only** partner whose video bids exceed its video callouts —
**2.29 against a next-highest of 0.26**, nearly 9x the field. On **display** it
is unremarkable (0.465, alongside PubMatic 0.346 and Media.net 0.334). So the
anomaly is not "Magnite" in general; it is **Magnite × video**.

**The sharper tell — and the theory it produced, which was then TESTED AND
REFUTED.** Magnite's video bids (118,929,248) and display bids (118,722,170) are
the same number to within **0.17%**. No other partner is close. That suggested
GAM was attributing one bid total to both yield groups, which would have made the
video figure a reporting artifact.

**It isn't.** Comparing the two *daily* rather than at window level
(`pull_magnite_ob_video_requests.py` prints this):

| days display bids == video bids exactly | **0 / 30** |
|---|---|
| days they agree within 1% | **1 / 30** |

Daily video/display bid ratios range **0.639 to 1.448**. The two series move
independently; the window-level match is coincidence. **The double-attribution
theory is dead, and GAM's video bid count is a genuine, independently measured
figure.**

Two consequences, and they run in the analysis's favour rather than against it:

**1. The responses↔bids match is restored as evidence.** An earlier revision of
this doc said that support should be dropped in case the bids column was a
double-attributed total. The test says it isn't, so the −3.0% agreement between
GAM's video bids (118.9M) and Magnite's reported ad responses (115.4M) stands.

**2. It yields an independent route to ~52M.** GAM measures **2.2855 bids per
video callout** for Magnite. Applying that rate to Magnite's *own* reported
115,402,553 ad responses implies **50,493,543 opportunities** — within **3.0%**
of GAM's 52,036,623 callouts. For Magnite's 265.8M request figure to be an
opportunity count instead, its bid rate would have to be 0.4341 responses per
request, i.e. **5.26x** below the 2.29 bids/callout GAM observes — against a
request ratio of **5.11x**. Those two ratios agreeing is exactly what you see
when one denominator is ~5x the other.

**What the video anomaly actually is**, then: Magnite genuinely returns ~2.3 bids
per video callout — multi-seat bidding — and is the only OB partner here that
does. That is a behavioural fact about Magnite's video integration, not a GAM
reporting fault, and it is not by itself a problem.

**AssertiveYield side — client-side bid rate, every SSP:**

| SSP | requests | bid rate |
|---|---|---|
| aps | 3,280,279 | 64.9% |
| **rubicon (Magnite)** | 3,280,347 | **48.6%** |
| ix | 3,280,334 | 46.8% |
| criteo | 2,575,753 | 45.0% |
| smilewanted | 2,575,813 | 43.5% |
| triplelift | 3,280,329 | 42.7% |
| openx | 2,133,315 | 42.1% |
| ttd | 3,280,329 | 42.1% |
| ozone | 3,280,296 | 35.7% |
| nativo | 1,086,635 | 33.5% |
| teads | 2,575,775 | 20.3% |
| pubmatic | 3,280,329 | 15.4% |

Every client-side bidder receives the same ~3.28M requests (Prebid fans out to
all of them), and **Magnite's bid rate is mid-pack**. Its *behaviour* is
unremarkable; only the GAM-side video **accounting** is strange.

**Consequence for this doc's own argument, stated plainly.** One of the three
supports for the 52.0M was "the bottom of the funnel reconciles — GAM video bids
118.9M vs Magnite's video ad responses 115.4M, −3.0%". If GAM's video-bids figure
is a cross-format total double-attributed to both groups, **that match is
coincidental and is not corroboration**. It should be dropped from the case. The
**impressions** match (4,762,385 vs 4,658,480, −2.2%) is independent of the bids
column and still stands, as does the ten-partner callout spread — which is the
support that actually carries the denominator.

**What this comparison cannot test.** We hold only *Magnite's* self-reported
seller numbers. Testing whether the 5.11x request gap is unique to Magnite —
rather than something every SSP's seller report does — needs the equivalent
"Seller Integration Type × Ad Format × Date" export from **PubMatic, Index and
OpenX** for the same window, compared against their own GAM callout counts
(51,852,880 / 52,081,338 / 51,624,004 video). That is one email each and it is
the single highest-value missing piece: if their reports also run ~5x GAM's
callouts, this is an industry-wide definitional difference and nobody is at
fault; if they come in at ~1x, the gap is Magnite's alone.

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
