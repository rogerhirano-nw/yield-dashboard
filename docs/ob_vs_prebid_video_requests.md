# OB vs Prebid Server video ad requests — both reports are right; the units differ

**2026-09-18, corrected 2026-09-22.** Magnite raised the Open Bidding video
ad-request volume "in comparison to PB server": their Seller-Integration-Type
chart shows Open Bidding at **265.8M** ad requests against **128.2M** for Prebid
Server (RP Hosted) on 2026-08-18 → 2026-09-16 video traffic, a 2.07x gap.

**GAM records 52.0M video callouts to Magnite in that window. Both numbers are
correct.** Ad Manager splits one video callout into several OpenRTB bid requests
before they reach the exchange — **bid flattening** — so Magnite receives and
counts ~5.11 requests per callout. `YIELD_GROUP_CALLOUTS` is measured *before*
the split, Magnite counts *after* it.

> ### Correction
> An earlier version of this doc concluded that **"Magnite's ad-request column is
> 5.11x what Google actually sent"** and that the gap was Magnite's to explain.
> **That was wrong and is withdrawn.** It rested on reading
> `YIELD_GROUP_CALLOUTS` as the number of requests the partner received. It is
> not — it is the number of *opportunities*. Google Partner Solutions established
> this on 2026-09-22 (Ishika, escalated case), and the mechanism is publicly
> documented. Our own data contradicted us and we did not check it: see
> **[The proof we already had](#the-proof-we-already-had)**. No approach was made
> to Magnite on the incorrect basis.

**The operative conclusion survives the correction, for a different reason.** OB
and PBS request counts are still not comparable one-for-one — not because either
is wrong, but because OB's are flattened ~5.11x and PBS's are not. Measured in
*opportunities*, **Prebid Server carries 2.46x more video volume than Open
Bidding** (128.2M vs 52.0M), and OB monetizes what it gets far better. See
[What it means for the comparison](#what-it-means-for-the-comparison).

## Bid flattening — the mechanism

Ad Manager splits a single video impression opportunity into several separate
OpenRTB bid requests before sending them to an exchange. Every split request
carries the same `BidRequest.ext.google_query_id`. Per Google's
[Flattened bid requests](https://support.google.com/authorizedbuyers/answer/9198190),
the split dimensions are:

| Dimension | What splits | Opt-out? |
|---|---|---|
| **Ad format** | one request per format (banner / native / video) | yes, RTB settings |
| **Video duration** | a request allowing both skippable and non-skippable becomes **two**, each with an adjusted max-duration | yes, RTB settings |
| **Video pods** | one request **per pod position** rather than one for the break | **no** |
| Deal type | PG and Preferred Deals separated | n/a — Google's help page states this **does not apply to Open Bidders** |

Each split request is a genuine, separately-identified request on the wire, so
an exchange counting them is counting correctly. The dimensions are all
video-specific, which is why **display shows no discrepancy**: there the two
counting levels coincide.

> **Note on one detail.** The escalation cited deal type as a contributing split
> dimension for our traffic; Google's own help page says deal-type flattening
> does not apply to Open Bidders. Duration and pods alone comfortably account for
> a ~5x factor, so nothing downstream depends on it — but prefer the help page.

**Opt-out is possible for format and duration** (via a Google technical account
manager) **but not for pods**. We have not opted out and there is no obvious
reason to: duration splitting is what lets buyers bid against a specific
duration. The only cost is reporting confusion, which this doc now covers.

## The reconciliation

GAM `YIELD_GROUP_CALLOUTS` / `BIDS` / `AUCTIONS_WON` / `IMPRESSIONS`, buyer
"Magnite fka Rubicon Project", `video` yield group, identical window
([run 35376751276](https://github.com/rogerhirano-nw/yield-dashboard/actions/runs/35376751276)):

| Metric | Magnite | GAM | Ratio | Reading |
|---|---|---|---|---|
| Ad requests / callouts | 265,819,907 | 52,036,623 | **5.11x** | **different units** — post-split vs pre-split |
| Auctions / auctions won | 99,800,117 | 36,487,791 | 2.74x | different units *and* different funnel stages |
| Ad responses / bids | 115,402,553 | 118,929,248 | **0.97x** | comparable — agree to 3.0% |
| Paid impressions / impressions | 4,658,480 | 4,762,385 | **0.98x** | comparable — agree to 2.2% |

The bottom of the funnel reconciles because both systems count it in the same
units. The top does not because they do not. **Impressions and revenue are the
figures to reconcile against a partner** — and Open Bidding is billed on Ad
Manager's totals anyway.

### The proof we already had

Five checks, all computable from data that was already in this doc on 2026-09-18.
Four of them confirm the split; the first refutes the original conclusion on its
own.

**1. The original model was arithmetically impossible.** Magnite reported
**115,402,553 ad responses**. If they had received only 52,036,623 requests, they
responded **2.22 times to every request they were sent**. A bidder cannot respond
more often than it is asked. The 265.8M denominator removes the impossibility:
115.4M responses on 265.8M requests is a **43.4%** response rate.

**2. Magnite's bid rate is the same on video and display, once the split is
removed.** Bid rate is a property of the bidder, and display has no split to
confuse it:

| | bids | requests | bid rate |
|---|---|---|---|
| Video, on Magnite's (post-split) denominator | 118,929,248 | 265,819,907 | **44.7%** |
| Display, on GAM's callouts (no split) | 118,722,170 | 255,316,495 | **46.5%** |
| Video, on GAM's callouts — *the original model* | 118,929,248 | 52,036,623 | **229%** |

44.7% against 46.5% is a 3.8% spread. 229% is not a bid rate.

**3. The split factor falls out of GAM's own data, without using Magnite's
number at all.** If Magnite bids at its display rate on video, then
`bids/callout ÷ display bid rate` = 2.285 ÷ 0.465 = **4.92 requests per callout**
— within **3.8%** of the 5.11 implied by Magnite's report. Two independent
routes to the same split factor.

**4. It is not multi-seat bidding.** The earlier explanation was that Magnite
returns ~2.3 bids per callout across seats. But GAM bids ÷ Magnite's *ad
responses* = 118,929,248 ÷ 115,402,553 = **1.031** — essentially one bid per
response. Multi-seat accounts for 3%, not 129%.

**5. The other nine partners' bid rates become sane, not anomalous.** Dividing
each partner's video bids/callout by the 5.11 split gives the rate at which they
actually bid on what they receive:

| Partner | video b/c | true bid rate |
|---|---|---|
| **Magnite** | 2.29 | **44.8%** |
| Media.net | 0.26 | 5.1% |
| PubMatic | 0.20 | 3.9% |
| OpenX | 0.19 | 3.7% |
| Index Exchange | 0.12 | 2.3% |

Google's escalation stated, without having seen our figures, that Magnite bids
"roughly 45%" and the others "0–5%". Both match. **This is why the effect looked
Magnite-specific**: at a 2–5% bid rate a 5x split still leaves bids/callout well
below 1, so the split stays invisible; only a ~45% bidder pushes the ratio past
1 and makes it show.

**6. Magnite's low OB auction rate is the split's signature.** OB converts 37.5%
of requests into auctions while every other integration runs 92–99%. Split
requests carry durations and pod positions a buyer may have no demand configured
for, so most are filtered before auction. 99.8M auctions ÷ 52.0M callouts =
**1.92 auctions per opportunity** — coherent. PBS (RP Hosted) at 98.2% is the
control: an unsplit channel.

**What the original analysis got right** was the opportunity count. Callouts run
47.3M–52.1M across all ten OB buyers, a 9.2% band, because OB calls everyone on
every opportunity. ~52.0M *is* the video opportunity count. The error was
equating opportunities with requests-received. The doc even wrote that Magnite
"cannot have received 265.8M requests out of ~52.0M auctions **unless each
auction sent it roughly five**" — which is exactly what happens.

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

## Google Ad Manager Support — two passes, the second overturns the first

### Pass 2 — Partner Solutions escalation (2026-09-22, Ishika) — AUTHORITATIVE

The case was escalated to Google's Partner Solutions Team, who reviewed it
against Open Bidding reporting behaviour. Their verdict:

> "Both your Ad Manager report and Magnite's report are correct. They disagree
> because the metrics you're comparing are counted at two different levels of
> granularity, and one specific Ad Manager behaviour on video inventory makes
> that difference much larger than it is on display."

**On the callout count** — directly reversing the pass-1 reading:

> "Yes, Ad Manager does send substantially more bid requests than this metric
> reports, and that is expected behaviour for video. Yield group callouts counts
> one callout per yield partner per impression opportunity; the actual number of
> OpenRTB bid requests sent is that figure multiplied by the split factor."

They explicitly ruled out the three benign mechanisms we had been chasing: **not
retries, not multi-slot or multi-impression bundling** (Ad Manager sends one
impression opportunity per bid request), and **not requests originating outside
the yield group**. It is solely the request splitting. They also called our
5.11 ratio "consistent with what we would expect for in-stream video with a
skippable/non-skippable duration split combined with deal-level separation".

**On bids > callouts**: counted per individual bid, after the split, while
callouts are counted before it — so >1 is expected on video. Magnite bids ~45%
on both display and video; "the 2.29 figure is not an elevated bid rate, it is
the same ~45% bid rate measured against a denominator that is ~5x too small."

**On auctions won**: counted per *winning bid*, derived directly from
`YIELD_GROUP_BIDS`, and recorded at ad-selection time **before the ad renders**.
A bid can win and never produce an impression (creative not returned, video
abandoned). So auctions-won and impressions are different stages *and* different
units, and the 13% we calculated is not a render rate.

**Their guidance on what reconciles with an exchange:**

| | Metrics | Our agreement |
|---|---|---|
| **Comparable** | impressions, revenue | within **2.2%** |
| **Comparable with care** | bids vs ad responses | within **3.0%** |
| **Not comparable** | callouts vs any exchange-side "ad request" / "available impressions" | — |
| **Not comparable** | auctions won vs impressions | — |

They cite an Ad Manager Help Center caveat to the same effect — "because video
pods can lead to multiple bid requests sent to third-party buyers, publishers
shouldn't expect these values to match the available impressions values for
third-party buyers." We were not able to locate that exact sentence on the
public Video-in-Open-Bidding page, so it is recorded as quoted to us rather than
as independently verified; the
[Flattened bid requests](https://support.google.com/authorizedbuyers/answer/9198190)
page independently documents the mechanism.

### Pass 1 — Ad Manager Support chat (2026-09-18, Aneesh) — SUPERSEDED

The first response said:

> "YIELD_GROUP_CALLOUTS counts every callout Ad Manager sends to a yield partner.
> Ad Manager does not send additional requests to partners for retries or
> multi-slot requests."

**Read literally this is still true, and it is still true that neither retries
nor multi-slot inflate the count.** What it does not say — and what we wrongly
inferred — is that the callout count equals the number of requests the partner
receives. Bid flattening is neither a retry nor a multi-slot bundle, so it sits
entirely outside the sentence, and the answer never addressed it. **Do not cite
pass 1 for the proposition that callouts are requests-received.**

The lesson worth keeping: a support answer that rules out the mechanisms *you*
proposed is not a confirmation that no mechanism exists. The question as asked
("is there any condition under which Ad Manager sends substantially more
requests than this metric reports?") was the right one; it needed the escalation
to get a complete answer.

Pass 1's second answer — that auctions-won is calculated against all bids
received — was correct and is reaffirmed by pass 2.

**Their aggregate agrees with ours.** Support ran callouts and bids for the buyer
and reported bids below callouts. Unsplit, our data says the same: 307,600,758
callouts against 237,651,418 bids, a ratio of 0.77. The >1.0 ratio appears **only**
when `YIELD_GROUP_NAME` is added as a dimension, and the split sums back to the
buyer total exactly — always state which cut you ran.

**Evidence sent:** `Magnite_OB_GAM_callouts_2026-08-18_to_2026-09-16.xlsx` /
`magnite_ob_gam_rows.csv` — the 60 per-day, per-yield-group API rows, generated by
`scripts/pull_magnite_ob_video_requests.py` with `CSV_OUT` set (the workflow
uploads it as a build artifact).

## Is the anomaly unique to Magnite? No — it is universal but only *visible* on Magnite

**Answered by the 2026-09-22 escalation.** Bid flattening applies to every Open
Bidding partner equally. It surfaces on Magnite's row alone because Magnite bids
~45% of the time while the other nine bid 2–5%: at a low bid rate, a 5x split
still leaves bids/callout below 1 and the effect stays hidden. The evidence
below is kept because it is what made the effect measurable — but read it as
*"Magnite is the only partner whose bid rate is high enough to expose the
split"*, not as a Magnite-specific defect.

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

Two consequences:

**1. The responses↔bids match is restored as evidence.** An earlier revision of
this doc said that support should be dropped in case the bids column was a
double-attributed total. The test says it isn't, so the −3.0% agreement between
GAM's video bids (118.9M) and Magnite's reported ad responses (115.4M) stands —
and bids-vs-responses is one of the two comparisons Google says is valid.

**2. The "independent route to ~52M" it was used for was circular, and the
numbers were already pointing the other way.** That argument applied GAM's
2.2855 bids-per-*callout* to Magnite's 115.4M responses to imply ~50.5M
opportunities — but bids-per-callout is only a bid *rate* if callouts are
requests, which is the thing being proved. **Withdrawn.**

What makes it worth keeping on the page is how close it came to the right
answer. It computed that for 265.8M to be the real request count, Magnite's bid
rate would have to be **0.4341** responses per request, i.e. **5.26x** below the
2.29 bids/callout GAM reports — "against a request ratio of **5.11x**" — and
then dismissed the agreement of those two ratios as what you see "when one
denominator is ~5x the other". That is precisely what was happening: 43.4% is
Magnite's true bid rate, and one denominator *is* ~5x the other. The arithmetic
was right and the conclusion drawn from it was backwards.

**What the video anomaly actually is**, then: not multi-seat bidding. Magnite
returns ~1.03 bids per ad response, so seats are a 3% effect. The 2.29
bids/callout is a normal ~45% bid rate measured against a denominator five times
too small.

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

**A worry that is now resolved.** An earlier revision feared that GAM's video
bids figure might be a cross-format total double-attributed to both yield
groups, which would have made the responses↔bids match coincidental. That was
tested and refuted (daily series agree exactly on 0/30 days), and the split
model explains the figure directly: 118.9M bids is a 44.7% bid rate on 265.8M
split requests. The bids column is real and is one of the two metrics Google
says *is* comparable with an exchange.

**The cross-SSP export is no longer needed.** This section used to end by
calling the equivalent "Seller Integration Type × Ad Format × Date" export from
PubMatic, Index and OpenX "the single highest-value missing piece" — the test of
whether ~5x-over-callouts is industry-wide or Magnite's alone. Google's
escalation answers it at the source: the splitting is Ad Manager's behaviour and
applies to all partners, so every exchange's video request count will exceed our
callout count by its own split factor. **Don't spend the three emails.** If one
is ever pulled for another reason, expect ~5x, not ~1x.

## What it means for the comparison

**This conclusion is unchanged by the correction.** The chart compares two
numbers in different units: OB's requests are flattened ~5.11x, PBS's are not.
Putting both channels on the same footing — **opportunities**, not wire requests
— means using GAM's callouts for OB and Magnite's own figure for Prebid Server:

| | Open Bidding | Prebid Server (RP) | |
|---|---|---|---|
| Video requests | 52,036,623 | 128,241,828 | PBS **2.46x** |
| Paid impressions | 4,658,480 | 2,398,427 | OB **1.94x** |
| Fill (impr / request) | **8.95%** | 1.87% | OB **4.8x** |
| Revenue | $39,333 | $31,026 | OB **1.27x** |
| Revenue / 1k requests | **$0.756** | $0.242 | OB **3.1x** |

Open Bidding is the *smaller* video **opportunity** channel and the *better* one
on every outcome measure. The chart's implied reading — that OB is consuming
outsized volume — does not survive putting both sides in the same units.

**The assumption this rests on, stated openly.** Prebid Server request counts
are taken as ~1 per opportunity, because PBS requests come from the page rather
than from Ad Manager, so Google's flattening never touches them. The supporting
evidence is PBS (RP Hosted)'s **98.2% auction rate** against OB's 37.5% — a
split channel strands most of its requests before auction, an unsplit one does
not. This has not been confirmed with Magnite, and it is the one number in the
table worth asking them to verify: *does your Prebid Server request column count
one request per auction?* If PBS requests were also multiplied, the 2.46x would
shrink.

**What to say to Magnite.** Their reporting is correct and so is ours; the two
columns in their own chart are not in the same units, because Ad Manager
flattens OB video requests and does not flatten Prebid Server's. The comparison
they drew — OB consuming outsized request volume — inverts once both are
expressed per opportunity. Nothing here is an error on their side, and the
reconciliation that matters commercially (impressions −2.2%, revenue) already
agrees.

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
