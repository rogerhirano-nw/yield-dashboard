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

### Ogury renders two completely different ways, and one is Mobkoi-shaped

| Slot | Creative | GPT iframe | Slot well | Max in-view | AV threshold |
|---|---|---|---|---|---|
| `dfp-ad-sticky` (2 of 2) | 1×1 | **0×0, `display:none`** | 390×50 | — | n/a |
| `dfp-ad-inarticle4` (mobile) | 1×1 | 390×730 + 390×1095 | 390×1095 | 77% | 30% (427,050px²) |
| `dfp-ad-inarticle7` (desktop) | 1×1 | 970×250 | 1440×250 | 100% | 50% |

**Every Ogury render on `dfp-ad-sticky` hides the GPT iframe at 0×0** —
across both sweeps, 4 for 4, all attributed by pbjs `bidWon` (not by the
weaker `hb_bidder` targeting). That is the Mobkoi breakout signature exactly:
GAM serves a 1×1, the measured iframe is hidden, and the unit is built
somewhere else. Ogury's in-article renders are the opposite — real iframes
filling real wells, comfortably over threshold. Same bidder, same page, two
render modes; only one of them is a measurement problem.

Three other renders tripped the hidden-iframe flag (aps, sovrn, rubicon —
one each) but they are a **different thing** and shouldn't be quoted as the
same finding: all three are 1×1 iframes at `display:block` attributed only by
`hb_bidder` targeting, i.e. almost certainly passback/tracking renders rather
than the bidder's ad. Ogury's are `0×0 display:none` under trusted
attribution.

### OneTag's banner render is clean; its video deficit is out of reach here

OneTag was caught once, on `dfp-ad-inarticle2` — a normal 300×250 iframe in a
390×250 slot, 100% in view. But OneTag's deficit is on **in-stream video**,
and **none of the 152 renders was video**: video plays in the page's IMA
player container, not a GPT slot, so the slot forensics structurally cannot
see it. The script now samples the player container's geometry and in-view
ratio on a timer and reports pbjs video `bidWon` separately, which is what
that case needs.

### SmileWanted could not be caught from this runner

**0 wins in 152 renders**, despite being ~5% of Prebid banner impressions and
configured on every in-article slot and sticky. The likely reason is the
runner's **US datacenter IP** — SmileWanted is a French SSP and its demand is
geo-weighted. Catching it on-page needs an EU egress (or a residential IP);
until then the GAM audit is the route for the largest of the four cases.

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
