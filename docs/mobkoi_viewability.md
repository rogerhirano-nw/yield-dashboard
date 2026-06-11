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

## What to do

For the flight / IO reporting:
1. **Report viewability from Mobkoi's own measurement** (their platform
   measures the rendered unit), or have the advertiser's MRC-accredited
   vendor (IAS/DV/MOAT) tag *inside* Mobkoi's unit at Mobkoi's end — not
   wrapped at the GAM creative layer.
2. If the GAM number gets challenged, the clicks-vs-viewable math above is
   the one-line rebuttal.
3. **Never sell/convert these LIs to vCPM** (viewable-impression goals) —
   GAM would bill ~nothing and delivery logic would crater. They are plain
   CPM today; keep it that way unless the measurement is fixed.

Structural, for future Mobkoi flights:
4. Ask Mobkoi for an **iframe-resident render mode**: the creative stays in
   the GPT iframe and the *iframe itself* is resized/positioned as the
   scroll-reveal window. AV then tracks the real geometry. (Their tag is a
   thin bootstrap — the render behavior is controlled by the `boot/<uuid>`
   config on their side, so this is a Mobkoi-AM conversation, not a
   trafficking change.)
5. Alternative publisher-side fix: a dedicated interscroller slot where the
   page provides the clipped container and the GPT iframe genuinely fills
   the clip window while scrolling. Web-team work; only worth it if Mobkoi
   volume grows.

Re-run the pull anytime: dispatch
`.github/workflows/diagnose_mobkoi_viewability.yml` (needs an open PR on
the branch for the comment step) — the LI list is at the top of
`scripts/diagnose_mobkoi_viewability.py`.
