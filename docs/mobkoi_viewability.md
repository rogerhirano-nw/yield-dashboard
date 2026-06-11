# Mobkoi interscroller/uniscroller — why GAM viewability reads ~1%

Diagnosed 2026-06-11 (PR #164; full pull in the PR comment thread). Applies
to the Mobkoi high-impact formats trafficked as third-party tags — at the
time of writing the in-flight Invesco IO1117 and Cartier IO1118 LIs:

| LI | Format | Lifetime imps | Clicks (CTR) | GAM AV viewable | AV measurable |
|---|---|---|---|---|---|
| 7310815861 | Invesco Interscroller | 78,537 | 445 (0.57%) | **0.51%** | 100% |
| 7313011338 | Cartier Interscroller | 45,153 | 358 (0.79%) | **0.45%** | 100% |
| 7316916920 | Cartier Uniscroller | 105,939 | 360 (0.34%) | **0.42%** | 100% |

## The number is a measurement artifact, not a delivery problem

The Invesco LI has **more clicks (445) than "viewable" impressions (400)**
on clean traffic (DV IVT ≈ 0.1%) — impossible if the ads were actually
invisible. CTR is healthy (interscroller-typical 0.3–0.8%) and uniform
across the `inarticle*` units. Users see and engage with these ads; Active
View just doesn't see what users see.

## Mechanism

- The creatives are 300x250 `ThirdPartyCreative`s, **SafeFrame off**, whose
  tag boots Mobkoi's renderer (`tagservice.maximus.mobkoi.com/boot/<uuid>`)
  inside GAM's friendly iframe in a standard in-article MREC slot.
- Mobkoi's renderer builds the full-screen scroll-reveal experience by
  injecting its own layer into the **parent page DOM** (that's why SafeFrame
  must be off) and effectively hides/collapses the original 300x250 iframe.
- **GAM Active View measures the GPT slot's iframe**, not the injected
  layer. The iframe is instrumentable (→ 100% measurable) but never meets
  50%-in-view-for-1s (→ ~0% viewable). Every impression scores
  measurable-but-not-viewable. The ~0.5% residue is most likely Mobkoi's
  in-banner fallback rendering inside the iframe when the scroller can't
  initialize.
- **DV agrees with GAM (1.5–2.5% viewable, ~98% measurable in
  `dv_attention`) because DV instruments the same GAM-served element.** Two
  vendors, one wrong element. DV's attention indices for these LIs
  (Exposure 9–19 vs 100 baseline) are equally meaningless. Don't read
  either as the unit's real viewability.

The pattern was identical on every day of the flight, every inarticle unit,
and every creative — structural, not a regression. (The oop1/2/3 hydration
bug in `docs/article_sponsor_logo.md` is unrelated: these serve in-article
slots.)

## Goal: make GAM's own Active View numbers real for these formats

The render *location* is what breaks AV, so no trafficking change on our
side can fix it (SafeFrame on would kill the unit entirely; size/placeholder
changes don't move what AV measures). The element the experience renders in
has to be the element AV measures. Three paths, in order of preference:

### 1. Mobkoi iframe-resident render mode (the real fix)

The precise ask to the Mobkoi AM/solutions team:

> Your interscroller/uniscroller tag currently hides the GAM friendly
> iframe and rebuilds the unit in a parent-DOM layer, so Google Ad
> Manager's Active View measures the hidden iframe: our GAM reporting
> shows your three live Newsweek LIs at 0.4–0.6% viewable / 100%
> measurable, while everything else on the same in-article slots measures
> 75.4% viewable (31M imps, 7 days). We need a build where the **creative
> experience stays inside the GAM iframe and your loader restyles/resizes
> the iframe element itself** (fixed/sticky + parent clip container) for
> the scroll-reveal — parent-DOM access for restyling the iframe is fine,
> moving the content out of it is what breaks measurement. Active View
> then tracks the real unit geometry, and both GAM and DoubleVerify
> (which instruments the GAM-served element) report true viewability.

Notes for that conversation:
- Their GAM tag is a thin bootstrap (`boot/<uuid>` config) — this is a
  render-mode flag on their side, not a retrafficking job for ad ops.
- Active View's threshold for "large" creatives (>242,500 px², which a
  full-viewport mobile unit is) is **30% of pixels for 1s**, so a
  scroll-through reveal passes comfortably once the iframe is the unit.
- Expected result: interscrollers measured this way report well above the
  75% display baseline (the format's whole pitch is ~full-screen exposure).

### 2. Verification loop (once they ship a build)

1. Have Mobkoi point a test `boot/<uuid>` at the new mode; traffic it on a
   `[TEST]` LI (the 2026-03 `Mobkoi-Publisher-Testing-*` LIs 7253027964 /
   7255084258 / 7256561225 can be reused) with a low goal.
2. Let it collect a few hundred impressions, wait a day (AV lags same-day).
3. Dispatch `.github/workflows/diagnose_mobkoi_viewability.yml` with the
   test LI id in the `line_item_ids` input — viewable% per day/ad unit
   lands as a PR comment (or in the run log if no PR is open on the
   branch).
4. Sane number (≥70%) → swap the live LIs' creative tags to the new build
   and re-verify; the daily table will show the step change.

### 3. Fallbacks if Mobkoi won't/can't

- **Publisher-owned scroller container**: the page provides the clipped
  container and the GPT iframe genuinely fills the clip window while
  scrolling; the creative fills the iframe. AV measures correctly for
  *any* vendor's scroller asset. Web-team work — worth it only if
  high-impact volume grows.
- **Tracking-LI viewable events** (GAM-report-native, but not Active
  View): Mobkoi fires a $0 GAM tracking pixel when their in-unit
  measurement deems the unit viewable; viewability = tracker imps / main
  LI imps in a two-line GAM report. Last resort — it puts a viewability
  number inside GAM reporting, but it isn't the AV columns and won't
  satisfy a buyer auditing Active View.

## Until the fix lands (this flight)

1. **Report viewability from Mobkoi's own measurement** (or the
   advertiser's MRC vendor tagged inside Mobkoi's unit at Mobkoi's end),
   not GAM/DV.
2. If the GAM number gets challenged, the clicks-vs-viewable math above is
   the one-line rebuttal.
3. **Never sell/convert these LIs to vCPM** (viewable-impression goals) —
   GAM would bill ~nothing and delivery logic would crater. They are plain
   CPM today; keep it that way until AV measures the real unit.
