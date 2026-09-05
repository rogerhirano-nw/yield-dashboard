# Prebid bidders reading below the Active View baseline

Raised 2026-09-04 from the GAM report *PreBid Display and Video, Aug 14 –
Sep 3 2026* (advertiser 5724335726, `hb_source` in `s2s` + `client`):
SmileWanted display, OMS display and OneTag video all read materially below
the rest of the wrapper. Ogury turned out to be a fourth, larger case the
report's pivot buried.

## The numbers (impression-weighted, whole window)

Weight matters: the workbook's `Sheet1` pivot averages the *daily* rates
unweighted, which is close but not the number to quote. Recomputed from the
raw rows as `Σ viewable ÷ Σ impressions`:

| Format | Bidder | Impressions | Viewable | vs baseline | Revenue | eCPM |
|---|---|---:|---:|---:|---:|---:|
| Banner | **smilewanted** (client) | 4,611,514 | **40.4%** | −35.4pp | $23,061 | $5.00 |
| Banner | **ogury** (s2s) | 2,827,225 | **54.4%** | −21.4pp | $10,900 | $3.86 |
| Banner | **oms** (s2s) | 38,097 | **56.3%** | −19.5pp | $232 | $6.08 |
| In-stream video | **onetag** (s2s) | 138,466 | **47.7%** | −38.4pp | $800 | $5.77 |
| Banner | *all bidders* | 85,241,109 | 75.8% | — | $364k | $4.27 |
| Banner | *excluding the three above* | 77,764,273 | **78.7%** | — | | |
| In-stream video | *all bidders* | 12,699,000 | 86.0% | — | $107k | $8.46 |
| In-stream video | *excluding onetag* | 12,560,534 | **86.5%** | — | | |

Two more banner bidders sit in the same neighbourhood and belong in the same
conversation: **kargo 64.2%** (1.19M imps) and **mobkoi 68.5%** (935k) — the
latter unsurprising given `docs/mobkoi_viewability.md`, and *not* the ~0.4%
the direct Mobkoi LIs used to read, so their wrapper demand renders
differently from the direct interscroller tags.

Scale of the prize: SmileWanted alone is 5.4% of Prebid banner impressions
and 1.63M viewable impressions short of baseline. Lifting just that bidder to
78.7% moves the **whole Prebid banner number from 75.8% to 77.8%**; fixing all
three banner cases lands it near 78.5%. Every one of these is a *good payer*
(smilewanted's $5.00 eCPM is above the $4.27 banner average, oms $6.08), so
the answer is almost certainly "fix the render or the placement", not "block
the bidder".

Both trends are **flat across all 21 days** (smilewanted 32–47% every single
day, ogury 47–62%, onetag 31–53%). Nothing regressed on a date — this is
structural, which also means it has been costing this all along.

## Is this the Mobkoi problem again? Not the same signature

`docs/mobkoi_viewability.md` diagnosed creatives that leave the GPT iframe and
render in the parent DOM: Active View measures the abandoned iframe, so those
LIs read **~0.4% viewable at 100% measurable** and the fix was the iframe
mirror. 40–56% is not that signature — a full breakout floors at ~0%, and a
partial one would have to be an implausibly precise mix of 0% and 80% days.

So before reaching for a mirror, the two candidate causes have to be told
apart, because they have completely different fixes:

* **MIX** — the bidder wins on slots/devices that are inherently less
  viewable (deep in-article positions, desktop rails, refreshed slots).
  Nothing is broken; it's a yield/floor conversation with the SSP.
* **RENDER** — on the *same* slot, device and size as everyone else it still
  measures worse. That's the bidder's own doing, and the Mobkoi playbook (ask
  for an iframe-resident build; mirror as the publisher-side fallback) applies.

Two facts from the Mobkoi work carry over and shape what counts as evidence:

* Active View needs **50% of the creative's pixels in view for 1 continuous
  second — 30% for creatives larger than 242,500px²**. A full-height
  in-article unit is "large", so it grades on the 30% rule; a 300×250 does
  not. Tall creatives are structurally harder to make viewable, and that is a
  legitimate, non-buggy reason for a lower number.
* **Rendering inside the measured iframe is the only way to move Active
  View.** There is no macro or API to declare viewability (tested to
  destruction in 2026-06 — see §1b of the Mobkoi doc). So if the render *is*
  the problem, the fix is the render.

## Tooling built for this (2026-09-04)

### 1. `scripts/prebid_viewability_audit.py` — the GAM side

Pulls Active View **eligible / measurable / viewable** by
`hb_bidder × ad unit × device × rendered creative size × format`, then runs
`dashboard_logic.viewability_mix_adjusted` over it. For each bidder that
re-weights its own cells to *its peers'* rates in those same cells and splits
the gap in two:

    expected = Σ (bidder_imps_in_cell × peer_rate_in_cell) / Σ bidder_imps
    mix_gap    = expected − peers_overall     ← what its placement mix costs
    render_gap = actual   − expected          ← what's left, i.e. its own effect

Both baselines are leave-one-out, so a bidder is never graded against its own
bad impressions (an early version wasn't, and a bidder alone in a cell showed
a fake positive "mix" advantage purely because it had dragged the site average
down — the unit test `test_viewability_mix_adjusted_excludes_self_from_baseline`
pins this). `mix_gap + render_gap` always reconstructs the total gap, which is
what makes the two numbers safe to quote separately in an SSP conversation.

The audit also reports **measurable rate** (measurable ÷ eligible) — a
creative rendering somewhere AV can't instrument shows up there, not in
viewable% — and a per-day series, to confirm structural vs regression.

`GAMClient._run_report` gained an optional `filters` argument for this (the
v1 REST `ReportDefinition.filters`), since `hb_bidder` reaches reporting as
the high-cardinality `KEY_VALUES_NAME` dimension (`{key}={value}`) and has to
be narrowed server-side.

Run: `.github/workflows/prebid_viewability_audit.yml` (dispatch; posts to the
PR, CSVs in artifacts) or locally with GAM creds in `.env`.

### 2. `scripts/prebid_render_forensics.py` — the on-page side

The Mobkoi DOM forensics, adapted to wrapper demand. GAM on-site preview
(`getPreviewUrl`) can't be used here — the GAM creative is the Prebid
universal creative, not the bidder's markup — so instead it loads **real
Newsweek article pages** in headless Chromium, instruments GPT and Prebid
*before they boot*, scrolls the article with dwell time so Active View's
1-second clock can actually run, and records per rendered slot:

* the winning bidder — from the pbjs **`bidWon`** event, which only fires when
  the Prebid creative really rendered. `hb_bidder` slot targeting is kept as a
  labelled fallback but is *not* trusted on its own: it names who won the
  client auction, and GAM may still have served AdX or a direct line over it.
* **`impressionViewable`** — GPT's own Active View verdict, client-side.
* the in-view% timeline from `slotVisibilityChanged`, compared against the
  50%/30% threshold for that creative's area.
* iframe-vs-slot geometry and computed style — the breakout signature is an
  iframe that is `display:none` / collapsed while the slot well stays open.
* large fixed/absolute nodes rendering *outside* every ad slot.

**Always run it against article pages.** The homepage runs a different slot
set (`homepage1`, `oop1`) and none of these bidders is configured on it; the
script scrapes fresh article URLs off the homepage and refuses to fall back
to the homepage itself.

Run: `.github/workflows/prebid_render_forensics.yml` (dispatch) or locally
with `CHROME_PATH` / `BROWSER_PROXY` set.

## What the live runs found (2026-09-04)

Two sweeps from a US datacenter IP, mobile + desktop emulation, article
pages only: 3 loads / 25 renders, then 20 loads / **152 renders**.

Wrapper shape, for the record: **Prebid 10.29.0**, s2s through **Magnite
Prebid Server** (`prebid-server.rubiconproject.com`, `mgnipbs` aliased to
rubicon, `allowUnknownBidderCodes: true` — which is how oms/onetag/ogury
arrive without appearing in the client adUnit config). Client-side bidders
per in-article slot are ttd, mgnipbs, aps, ozone, pubmatic, ix, rubicon,
nativo, triplelift, teads, criteo and **smilewanted**. Slots are
`dfp-ad-inarticle1…10`, `dfp-ad-sticky`, `dfp-ad-interstitial` and the IMA
video player.

### Ogury: the sticky slot collapses, and it is NOT a breakout

The iframe **ids** settle what the geometry alone could not. `google_ads_iframe_*`
is the GPT-served frame — the one Active View measures; anything else is a
vendor frame.

| Slot | Iframes present (id → size, display) | Verdict |
|---|---|---|
| `dfp-ad-sticky` (2 of 2, 152-render sweep) | `google_ads_iframe_…/sticky_0` → **0×0, `display:none`**, and nothing else | **collapsed** |
| `dfp-ad-sticky` (later run) | `google_ads_iframe_…/sticky_0` → 300×50, visible | healthy |
| `dfp-ad-inarticle4` (mobile) | `ogy-iframe-wm-hb-iart-…` → 390×730 visible **+** `google_ads_iframe_…/inarticle4_0` → **390×1095 visible** | healthy |
| `dfp-ad-inarticle7` (desktop) | `google_ads_iframe_…/inarticle7_0` → 970×250 visible | healthy |

**On the collapsed sticky renders the GPT iframe is the *only* iframe in the
slot, and it is hidden at 0×0.** Win-time DOM snapshots (400ms after
`bidWon`, before anything can be torn down) confirm there is no Ogury node
anywhere outside the ad slots and no vendor iframe inside the slot. So
nothing renders in its place: **the impression is counted, no ad is shown,
and Active View reports it non-viewable — correctly.**

That is the opposite of the Mobkoi diagnosis. Mobkoi's number was *wrong*
because a real, visible unit was being measured in the wrong element. Here
the number is *right*: there is nothing to view. The distinction decides the
fix, so it is worth stating plainly — **do not mirror this.** An iframe
mirror on a slot with no content behind it would manufacture viewable
impressions for an ad that was never shown, which is precisely the thing the
mirror was careful *not* to do.

Ogury's in-article renders are healthy and need nothing: the GPT iframe stays
visible and large (390×1095 mobile — it, not the vendor's 390×730 frame, is
what AV measures; 970×250 desktop), reaching 65–77% in view against a 30%
large-creative threshold.

**Mechanism, pinned down (10-load run, ogury won sticky on 6 pages):**
Patching the style and attribute paths to capture a stack trace whenever an
`google_ads_iframe_*` element is hidden or zeroed caught **nothing** — across
8 blank renders, no script ever hid the frame. Win-time snapshots (400ms
after `bidWon`) show it already at 0×0 `display:none` on the failures and
already 300×50 visible on the successes. So the frame is not *hidden*; it is
**never revealed** — GPT creates it hidden and shows it when the creative
renders, and on these impressions the creative never renders. Nobody breaks
anything; Ogury's sticky creative simply fails to paint.

The failure is **page-consistent and frequent**: the sticky slot refreshes
(2 renders/page), and both renders on a page share the outcome — 5 of 6
pages blank on both, 1 of 6 fine on both, i.e. **8 of 10 sticky renders
blank**. It is not "the second render fails", and it does not correlate with
Ogury also winning an in-article slot on the same page (two all-blank pages
had no in-article Ogury at all). Their in-article renders on those same
pages work fine, so their SDK is alive on the page — it is the sticky
creative specifically.

**So the fix for Ogury is, in order:**
1. **Blank-detect + refresh on `dfp-ad-sticky`, excluding Ogury on the
   retry** — reference implementation in
   `docs/snippets/sticky_blank_detect_refresh.js` (site ad-stack work, not
   in this repo's runtime).** The failure is precisely detectable from the page: ~1.5–2s after
   `slotRenderEnded`, if the slot's `google_ads_iframe_*` is still 0×0 /
   `display:none`, the impression is blank. Refresh the slot so a bidder who
   renders fills it. **The retry must exclude the bidder that just blanked** —
   the page-consistent double-blank above is exactly what happens when the
   refresh re-serves Ogury. This needs no vendor cooperation and *keeps* the
   revenue that dropping them would forfeit.
   Honest limit: the blank impression is already counted, so this does not
   erase it — it converts a dead impression into a second, live one
   (slot blended ≈ (0 + viewable)/2 rather than 0). Keep it scoped to
   blank-detection rather than becoming a general refresh.
2. **Send Ogury the bug report in parallel.** The reproduction is tight and
   hard to dismiss: 8 of 10 sticky wins paint nothing, the GPT iframe never
   leaves its pre-render state, *no script in the page hides it*, and their
   own in-article creatives render fine on the same pageviews.
3. **Drop them from sticky** only if (1) fails to recover the slot and (2)
   stalls.

**Do not apply the iframe mirror** (above): there is nothing behind the
frame to reveal.

**Ruling out the harness as the cause.** The evidence above comes from a
headless browser on a US datacenter IP with a privacy banner on screen — all
of which could, in principle, make a creative refuse to paint and mimic this
exactly. Two things close that off:

* **The banner is notice-only.** Newsweek's Ketch banner offers just *Privacy
  Policy* and *Manage Preferences* — there is no accept control, because it
  is a US state-privacy notice rather than a GDPR consent gate. Consent is
  not being withheld, so it cannot be why a creative declines to render.
  (An attempt to "accept" it therefore dismissed 0 of 10 banners; the script
  now reports the controls it found and says so explicitly, rather than
  leaving that looking like a failed click.)
* **The same-pageview control.** On pageviews where Ogury's sticky render
  went blank, Ogury's *in-article* creatives rendered fine — same page, same
  consent state, same IP, same browser, same SDK, same auction. Any
  environmental explanation would have to suppress one slot and spare
  another on the same load, which consent, IP and headless-ness do not do.

So the failure is specific to Ogury's sticky creative, not to the test
environment. What the harness still cannot establish is the **production
rate** — that is the GAM audit's per-ad-unit cut, and it is what turns this
from a reproduced defect into a sized one.

Sample caveat: 2 collapsed and 1 healthy sticky render observed. The
collapse is real and reproducible, but its *rate* — which is what decides
how much of the 21pp gap it explains — comes from the GAM audit's per-ad-unit
cut, not from this. If Ogury reads near 0% on `sticky` and 80%+ in-article,
the story is confirmed and the fix scopes to one slot.

### OneTag's banner render is clean; its video deficit is out of reach here

OneTag was caught once, on `dfp-ad-inarticle2` — a normal 300×250 iframe in a
390×250 slot, 100% in view. But OneTag's deficit is on **in-stream video**,
and **none of the 152 renders was video**: video plays in the page's IMA
player container, not a GPT slot, so the slot forensics structurally cannot
see it. The script now samples the player container's geometry and in-view
ratio on a timer and reports pbjs video `bidWon` separately, which is what
that case needs. Confirmed working on 2026-09-04: the page's player shell is a
**`<mux-player id="nw-video-player">`** custom element (390×219 on mobile),
and the ads render in a **`.nw-ima-ad-container`** inside it holding 4
iframes and 2 `<video>` elements, sampled ~95× over one article scroll at
100% max in-view. The script resolves the ad container in preference to the
shell — a comma-separated `querySelector` can't express that preference
(it returns the first match in *document* order), which is how a first cut
ended up measuring the shell and reporting 0 iframes. No video bid was won in that short sample — video
wins are rarer than banner, so catching onetag there needs volume, same as
smilewanted.

### SmileWanted never bids from this runner — so it cannot be caught here

Instrumenting the auction (rather than sampling more pages) answered this in
one run of 4 articles × 2 profiles:

    bidder          requested   bids  noBid  timeout  error  wins
    smilewanted            67      0     67        2      7     0

**Requested on every auction, essentially never bids.** A later 10-load run
put it at **1 bid in 70 requests** (and 2 wins off that one bid), so the
right statement is a ~1% bid rate from this runner, not a flat zero — an
earlier note here said "never bids", which the second run disproved. Either
way it is not losing on price: the demand barely arrives, so catching a
render is a matter of many loads and luck rather than sampling technique.
(Confirmed across both device profiles: the 152-render sweep was 75 mobile +
77 desktop, iPhone emulation with touch, and SmileWanted won zero in each.)
Leading suspect is the runner's **US datacenter IP** against a French SSP;
the **Ketch consent banner** visible on these loads is a second candidate,
since consent state gates which bidders are called.

**Every render it has produced is healthy.** Four captured so far across the
sweeps, on two different slot types, none showing the Ogury failure mode:

| Slot | GPT iframe | Blank? |
|---|---|---|
| `dfp-ad-sticky` ×2 | 320×50 `display:inline`, 100% in view | no |
| `dfp-ad-inarticle6` | 300×250 `display:block` | no |
| `dfp-ad-inarticle7` | 300×250 `display:block` | no |
| `dfp-ad-inarticle2` ×2 | 390×400 `display:block` | no |

Six renders against 4.6M impressions cannot clear a bidder, and they are all
from a datacenter IP on the rare occasions it does bid — plausibly not the
traffic that makes up its 40.4%. But the evidence points **away** from a
render defect and toward **placement mix**, which is the opposite of Ogury and
means the GAM audit's mix-vs-render split is the deciding test rather than
more page loads.

Consequences: seeing how SmileWanted renders needs a browser on an
EU/residential IP — a colleague loading an article with `?pbjs_debug=true`,
or this script run from an EU egress. Failing that, the GAM audit still
answers whether its 40.4% is placement mix or bidder-specific; it just
cannot supply the DOM picture.

Two more results from the same table:
* **onetag bids but loses** — 4 bids, median $0.48, 0 wins. Catchable with
  more loads, unlike smilewanted.
* **oms was never requested** on these slots at all, consistent with its tiny
  38k volume.

Reading note: s2s bidders show `requested 0` because the client only requests
`mgnipbs` (Magnite Prebid Server) and PBS-sourced bids return under their own
bidder codes. Expected, and it confirms attribution works.

### Limitation worth knowing before quoting this harness

`impressionViewable` fired on **146 of 152** renders. That is not a
viewability estimate — the script scrolls the whole article with dwell time,
i.e. it behaves like a perfect user, so nearly everything measurable ends up
viewable. Treat the GPT event as proof that a slot **can** be measured and
the geometry as proof of **how** the creative renders; the rate itself comes
from the GAM audit, never from this.

## How to read the results (decision rules)

1. **`render_gap` near zero, `mix_gap` strongly negative** → placement. Take
   it to the SSP as a mix/floor conversation, or accept it. No creative work.
2. **`render_gap` strongly negative + on-page shows a hidden/collapsed iframe
   while content renders outside it** → Mobkoi-class. Ask the SSP for an
   iframe-resident build first (that conversation now has a proven precedent
   and before/after numbers); the publisher-side **iframe mirror**
   (`docs/snippets/mobkoi_iframe_mirror_creative.html`) is the fallback — but
   note it was applied to *our own* third-party-tag creative, and wrapper
   demand renders through the Prebid universal creative, so a mirror here
   would have to live in the wrapper's render path, not in a GAM creative.
   Scope that before promising it.
3. **`render_gap` negative and the creative is simply very tall** (large-
   creative 30% rule, in-view topping out in the 60s) → real, expected, and
   the honest answer to a buyer asking why the number is low. Worth knowing
   before anyone "fixes" it.
4. **Low measurable rate rather than low viewable rate** → an instrumentation
   problem (cross-domain render AV can't measure), a different fix again.

## Never do these (carried over from the Mobkoi debrief)

* Never sell or convert affected inventory to **vCPM** while the number is
  under investigation — GAM would bill against a broken measurement.
* Never try to declare viewability with `%%VIEW_URL_UNESC%%` or any macro; it
  counts impressions for out-of-page creatives, not viewable impressions, and
  the 2026-06 live test returned a clean null result.
