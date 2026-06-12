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
has to be the element AV measures. The paths, in order of preference (and
one tested dead end kept for the record):

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

### 1b. Dead end, tested: you cannot declare viewability to GAM

We tried the obvious publisher-side hack — append a watcher to the Mobkoi
tag that implements the MRC criteria itself (parent-document
IntersectionObserver, 50%-for-1s / 30% for large units) and pings GAM's
`%%VIEW_URL_UNESC%%` macro when met. **It does not work, by design.**
Tested live 2026-06-11→12: creative 138562143597 (`DIRECT- NEWSWEEK
(modified)`) on the Invesco LI served ~1k impressions with the watcher
armed and confirmed serving (snippet stored intact, EVEN rotation) — and
read 0.50% viewable vs 0.47% for the untouched tag. Null result.

Per Google's macro documentation, the view URL macro is **delayed
impression counting for out-of-page creatives** — it lets Ad Manager
"count an impression each time a creative is downloaded… and has begun to
load," and is documented "only for out-of-page creatives"
(support.google.com/admanager/answer/2376981). It books *impressions*,
never viewable impressions, and **no macro or API exists to declare
Active View viewable impressions** — AV is MRC-accredited
Google-measured; publishers can't write into it. The watcher's pings
appear deduped against the already-counted impression (no impression
inflation in the 6/11 data), so the modified creative is harmless but
useless — strip the watcher block from 138562143597 or pause that
creative.

Two corollaries survive the dead end:
- **Rendering inside the measured iframe is the only way to move Active
  View** — which the natural experiment below proves works for takeover
  formats (ClipCentric 58–67% on our own homepage).
- The in-view watcher logic itself is sound and reusable for the
  **tracking-LI proxy** (fallbacks below): point the ping at a $0 GAM
  tracking line item's impression tag instead of the view macro, and
  in-view% = tracker imps / main-LI imps in a two-row GAM report (clearly
  labeled ours, not Active View). Two hard-won implementation rules: the
  watcher must live in the **parent document** (breakouts destroy the
  iframe realm and its observers/timers — why the 970x250_FullBleed
  observer never fired), and use the **30% threshold for elements
  >242,500 px²** (a full-viewport unit can never reach 50% of its own
  area on small screens).

### 1c. Iframe mirror — publisher-side fix, validated viable by DOM forensics

Since AV scores the GPT iframe's *geometry*, the iframe itself can be made
to track the unit — no Mobkoi cooperation needed. Checked live on
2026-06-12 by minting on-site preview URLs via SOAP
`LineItemCreativeAssociationService.getPreviewUrl` and rendering them in
headless Chromium (`.github/workflows/preview_mobkoi_dom.yml`, mobile
emulation, screenshots in run artifacts):

- Mobkoi **hides** the GPT iframe (`display:none`) — it stays in the DOM,
  so it can be restyled. (Detached would have killed this path.)
- Their unit renders in `div#mobkoi-<digits>` whose box is
  **pixel-identical to the slot div** (`dfp-ad-inarticleN`, grown to a
  390×844 viewport-height well). The slot div IS the unit's window, so
  mirroring needs no Mobkoi-specific selectors.
- Stack intel: the creatives are **Celtra**-built (`cdn.celtra.com`),
  wrapped in Mobkoi's own advertiser-side DV (`dvtp_src.js?sid=mobkoi`)
  and `xpln.tech` analytics — all untouched by the mirror. The payload
  rendered on a US runner, so previews aren't geo-blocked.

The mirror (append to the vendor tag —
`docs/snippets/mobkoi_iframe_mirror_creative.html` is the full creative):
parent-document script waits for the breakout signature (our iframe going
`display:none`; in-iframe fallback banners are left alone so their clicks
keep working), then force-shows the iframe + its GPT container as an
absolute transparent pointer-events:none fill of the slot div, re-asserted
every 500ms for ~2 min. AV then scores the well users actually scroll
through — expect viewability to land near (or above) the 75% display
baseline. This fixes the **GAM-served-layer** measurement (AV, and DV
Pinnacle if it instruments the GAM layer); it should be done openly — the
slot frame mirrors the unit's window, reporting true exposure.

Verify the same way as the watcher test: swap the mirror into creative
138562143597 (replace the dead watcher block), let it serve a day, then
dispatch the diagnose workflow and watch the per-creative viewable% split.

**Status: live on all three Mobkoi creatives.** Applied 2026-06-12 via
`.github/workflows/apply_mobkoi_iframe_mirror.yml` (dry-run by default,
`apply=true` to write; scoped by `creative_ids`, idempotent). Geometry
validated same day (preview run 27389132646): on all five inarticle
slots, iframe == slot div == Mobkoi unit at 390×844, wells back in
normal flow, vendor stack untouched. v1 lesson encoded in the snippet:
absolutize ONLY the iframe — the GPT container props the well height
(run 27388975990 collapsed it). **First AV evidence (run 27409601588):**
the mirror's first ~240 impressions — the 2–4am-UK tail of report-day
6/11 — measured **~34% viewable vs 0.47%** for the untouched tag on the
same LI, the first AV movement this format has ever shown, so the
mirror was rolled to the Cartier creatives (138558786242, 138558555303)
the same morning (run 27410237495). **Control retired:** once the A/B was
banked, the un-mirrored original (138557481462) was deactivated on the
Invesco LI (run 27411814804, reversible LICA deactivation via
`retire_mobkoi_control.yml`) — all remaining Mobkoi delivery is
mirrored. Clean full-day read: the 6/13 diagnose pull on all three LIs.

Pulling the homepage takeover/insight LIs through the same diagnostic gave
a clean A/B — same site, same homepage slots, overlapping flights:

| Creative | Render path | Imps | AV viewable |
|---|---|---|---|
| Infiniti "Desktop 1" / "Infinity mobile" (ClipCentric Center Stage third-party tags, LI 7311682075) | in/around the GAM iframe | 59k / 88k | **66.9% / 58.6%** |
| Infiniti "Mobile 2", "Homepage Takeover Desktop 2/3/4", "Homepage 3 Mobile", "Mobile 4" (CustomCreative `addImageToHomepage` innerHTML injection, no observer/ping; same LI) | parent-DOM injection | 9k–36k each | **0.00% each** |
| Homepage Insight_Fluid (fluid native template, SafeFrame on, LI 7316340383) | in-iframe | 50k | **61.6%** |
| Kia Homepage-Insight (template injecting into `dfp-ad-homepage3`, LI 7226895315) | parent-DOM injection | 54k | **0.00%** (3-month sold flight) |
| 970x250_FullBleed test (injection **with** the view-macro observer, LI 7333906212) | parent-DOM injection + view-macro ping | 5 | 0% — see below |

Takeaways:
- **In-frame rendering measures organically — no macro needed.** ClipCentric
  runs full takeover formats at 58–67% AV on our own homepage; that's the
  precedent to quote at Mobkoi for path 1 ("ClipCentric's Center Stage
  renders measurably; we need the same from your scroller").
- ClipCentric's tag comment says `Tag Type: GAM no view macro` — that
  refers to the same out-of-page *impression* macro covered in 1b, not a
  viewability mechanism.
- Every parent-DOM injection reads exactly 0.00%. Same artifact class as
  Mobkoi.
- The FullBleed test (0/5 viewable) had two independent problems: its
  observer died with its own iframe (`addImageToSlot` runs
  `el.innerHTML = …` — wiping the slot div's children **including the GPT
  iframe the script lives in** — before `trackViewability` ever runs), and
  even a live observer pinging `%%VIEW_URL%%` couldn't have moved AV (see
  1b). For any injected format, expect 0% AV; the realm-death and
  30%-threshold lessons still apply to tracking-pixel watchers.

### 2. Verification loop (once Mobkoi ships a build)

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
- **Tracking-LI in-view events** (GAM-report-native, but not Active
  View): a $0 GAM tracking pixel fired when the unit meets MRC criteria —
  either by Mobkoi's own measurement, or by our parent-document in-view
  watcher from 1b with the ping pointed at the tracker instead of the view
  macro. In-view% = tracker imps / main-LI imps in a two-line GAM report.
  It puts the number inside GAM reporting, but it isn't the AV columns and
  won't satisfy a buyer auditing Active View.

## Until the fix lands (this flight)

1. **Report viewability from Mobkoi's own measurement** (or the
   advertiser's MRC vendor tagged inside Mobkoi's unit at Mobkoi's end),
   not GAM/DV.
2. If the GAM number gets challenged, the clicks-vs-viewable math above is
   the one-line rebuttal.
3. **Never sell/convert these LIs to vCPM** (viewable-impression goals) —
   GAM would bill ~nothing and delivery logic would crater. They are plain
   CPM today; keep it that way until AV measures the real unit.
