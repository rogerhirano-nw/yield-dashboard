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

### 1b. Publisher-declared views — our homepage-takeover trick, applied to their tag

GAM's **view URL macro** (`%%VIEW_URL_UNESC%%`) exists for exactly this
scenario: a creative that renders outside the measured frame implements the
MRC criteria itself (IntersectionObserver, 50%-for-1s) and pings the macro
URL, and GAM books a viewable impression on the line. The NW homepage
full-bleed injection creative already does this, so the same logic can be
appended to the Mobkoi creatives **without touching their tag**: their
snippet is editable text in GAM — add a second `<script>` after it that
plants a parent-document watcher observing the slot div (the unit's scroll
window) and fires the ping. Mobkoi's bootstrap, consent macros, and click
tracking stay byte-identical.

**Live test (2026-06-11): creative 138562143597 on the Invesco LI
7310815861 carries this watcher.** The diagnose workflow runs daily by
cron (once merged to main) and posts the per-creative AV table to the open
PR — watch whether the new creative's viewable% separates from the
original tag's ~0.5%. First meaningful read: 6/13 reporting on 6/12.
Ready-to-paste full creative: `docs/snippets/mobkoi_declared_view_creative.html`.

Watcher template (append after Mobkoi's `<!-- END TAG -->`):

```html
<script>
(function () {
  try {
    var PING = '%%VIEW_URL_UNESC%%';
    var slot = window.frameElement &&
               window.frameElement.closest('[id^="dfp-ad-inarticle"]');
    if (!slot || PING.indexOf('%%') === 0) return;  /* macro didn't substitute */
    /* Watcher lives in the PARENT document: the vendor tag hides/kills this
       iframe, and observers/timers in a destroyed realm silently die. */
    var D = window.parent.document;
    var boot = D.createElement('script');
    boot.textContent = '(' + function (sel, ping) {
      var el = document.querySelector(sel);
      if (!el) return;
      var fired = false, timer = null;
      var io = new IntersectionObserver(function (es) {
        var e = es[es.length - 1];
        /* Mirror Active View's large-creative rule: >242,500 px^2 needs 30%
           in view, not 50% — a full-viewport unit can never reach 50% of its
           own area on small screens. */
        var r = e.boundingClientRect;
        var need = (r.width * r.height > 242500) ? 0.3 : 0.5;
        if (!fired && e.isIntersecting && e.intersectionRatio >= need) {
          if (!timer) timer = setTimeout(function () {
            fired = true; io.disconnect(); new Image().src = ping;
          }, 1000);
        } else { clearTimeout(timer); timer = null; }
      }, { threshold: [0.3, 0.5] });
      io.observe(el);
    } + ')(' + JSON.stringify('#' + slot.id) + ',' + JSON.stringify(PING) + ');';
    (D.head || D.documentElement).appendChild(boot);
  } catch (e) {}
})();
</script>
```

Caveats, in test order:
- **Macro substitution**: the view macro is documented for *custom*
  creatives; Mobkoi's are `ThirdPartyCreative`s. If GAM doesn't substitute
  it there, the watcher self-disarms (the `%%` guard) — test on a `[TEST]`
  LI and check whether viewable impressions move. If it doesn't
  substitute, re-traffic the same tag inside a CustomCreative — but then
  verify the `${GDPR}`/`${GDPR_CONSENT_898}` consent macros still resolve
  (they're third-party-creative macros; UK flights break without consent).
- **Slot-div geometry**: the watcher observes the slot div as a proxy for
  the unit's scroll window. If Mobkoi collapses the slot div to 0-height,
  the observer never fires — check the rendered DOM once (GAM preview) and
  switch the selector to their injected container if needed.
- **Self-declared ≠ third-party-verified**: declared views make *our* GAM
  reporting truthful; an agency auditing with DV/IAS still sees the broken
  number until path 1 lands. Use for our metrics/health, not as the
  billing-grade answer to a vendor-measured IO.

Hardening notes from the homepage creative (apply there too): don't
`innerHTML`-wipe the slot div from inside your own iframe **before**
handing the watcher to the parent — replacing the slot's children destroys
the GPT iframe your script (and its observer/timeout) lives in, so the
ping never fires on some browsers; plant the parent-document boot script
*first* (same lesson as the sponsor-logo creative). And use the
large-creative 30% threshold above — a 100vw×auto image on mobile can
exceed 242,500 px² and a 0.5 ratio becomes unreachable.

### Proof both paths work — the homepage natural experiment (run 2026-06-11)

Pulling the homepage takeover/insight LIs through the same diagnostic gave
a clean A/B — same site, same homepage slots, overlapping flights:

| Creative | Render path | Imps | AV viewable |
|---|---|---|---|
| Infiniti "Desktop 1" / "Infinity mobile" (ClipCentric Center Stage third-party tags, LI 7311682075) | in/around the GAM iframe | 59k / 88k | **66.9% / 58.6%** |
| Infiniti "Mobile 2", "Homepage Takeover Desktop 2/3/4", "Homepage 3 Mobile", "Mobile 4" (CustomCreative `addImageToHomepage` innerHTML injection, no observer/ping; same LI) | parent-DOM injection | 9k–36k each | **0.00% each** |
| Homepage Insight_Fluid (fluid native template, SafeFrame on, LI 7316340383) | in-iframe | 50k | **61.6%** |
| Kia Homepage-Insight (template injecting into `dfp-ad-homepage3`, LI 7226895315) | parent-DOM injection | 54k | **0.00%** (3-month sold flight) |
| 970x250_FullBleed test (injection **with** the view-macro observer, LI 7333906212) | parent-DOM injection + declared views | 5 | 0% — see below |

Takeaways:
- **In-frame rendering measures organically — no macro needed.** ClipCentric
  runs full takeover formats at 58–67% AV on our own homepage; that's the
  precedent to quote at Mobkoi for path 1 ("ClipCentric's Center Stage
  renders measurably; we need the same from your scroller").
- ClipCentric's tag comment says `Tag Type: GAM no view macro` — they ship
  a **view-macro tag variant** as a product option, i.e. declared views in
  third-party tags are an established vendor pattern (good sign for the 1b
  watcher on Mobkoi's tag, and worth requesting from ClipCentric too when
  their format does break out).
- Every parent-DOM injection without declared views reads exactly 0.00%.
  Same artifact class as Mobkoi.
- **The FullBleed test's declared views aren't landing** (0/5 viewable and
  one impression belonged to a click-tester who certainly looked at it).
  The snippet explains it: `addImageToSlot` runs `el.innerHTML = …` first —
  wiping the slot div's children **including the GPT iframe the script
  itself lives in** — and only then calls `trackViewability`, so the
  observer + 1s timer are created in a destroyed realm and never fire
  reliably. Fix = parent-boot pattern above (injection *and* observer in
  the parent realm) + the 30% large-creative threshold. n=5 is tiny —
  re-test after fixing and re-dispatch the workflow with LI 7333906212.

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
