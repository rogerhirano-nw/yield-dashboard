# GAM placement injection — serving ads at arbitrary page positions

The technique behind two shipped placements: render an ad **anywhere in the
article DOM** with zero page-side changes — campaign, creative, targeting,
impression counting, and click tracking all live in GAM. Sibling doc:
`docs/article_sponsor_logo.md` (the breadcrumb sponsor logo, same stack).

## The pattern

1. **Carrier slot**: a GPT slot that reliably renders on the target
   template. On Next.js article pages that's `inarticle1` (its div lives in
   the raw article-body HTML React never reconciles; renders at/near load,
   desktop + mobile) or `interstitial` (client-rendered lazy wrapper;
   renders on first scroll). The `oop1/2/3` units do NOT render on article
   templates (hydration deletes their divs — see the sponsor-logo doc).
2. **Deterministic delivery**: a dedicated SPONSORSHIP line item at
   **priority 3** targeting only the carrier unit (+ the demo/flight
   key-value), so it always outranks the priority-4 campaign LIs for that
   slot. Single-size placeholder doubles as a device filter: a 970x250-only
   LI matches only desktop `inarticle1` requests (mobile requests are
   300x250-only), leaving mobile untouched.
3. **Wrapper CustomCreative, SafeFrame OFF** (friendly iframe → parent DOM
   access). Its code:
   - hides its own carrier slot (`#dfp-ad-<slot>-wrapper` → `display:none`),
   - finds the positional anchor with a retry loop (~20 × 300ms),
   - inserts a container at the target position — for oversized formats use
     the house breakout (`width:100vw; left:50%;
     transform:translateX(-50%)`, centered flex, `min-height` reserved),
   - renders the payload. A **third-party tag** goes into a fresh friendly
     iframe via `document.open()/write()/close()` — the same mechanics GPT
     uses, so write-based tags (Innovid, DCM) work. First-party content
     (logo strip) can be plain DOM.
   - **once-guard on a parent-window flag** (`window.__nw…`) so re-renders
     and watcher retries never double-render or double-fire trackers.

## Tracking semantics

- GAM counts the **carrier render** as the impression; clicks flow through
  `%%CLICK_URL_UNESC%%`/`%%CLICK_URL_ESC%%` as usual.
- GAM macros (`%%CACHEBUSTER%%`, click macros, `${GDPR}`/`${GDPR_CONSENT_*}`)
  expand in the wrapper snippet exactly as in a ThirdPartyCreative — embed
  agency tags verbatim.
- Inside a `<script>` block, split any embedded `</script>` as
  `'</scr' + 'ipt>'` or the HTML parser terminates the wrapper's own tag.

## Viewability

**GAM Active View is junk for injected placements** — it measures the
carrier slot's iframe, which is hidden (or a 2x1 speck), while the visible
content lives elsewhere in the DOM. Expect ~0% viewable on these LIs in GAM
reporting and warn agencies before they read it as breakage. Measurement
that runs *inside* an injected friendly iframe (IAS/DV/MOAT wrappers in an
agency payload) measures the true on-screen position and reports correctly.

Mitigations, by rigor:

1. **House MRC beacon (live on the Infiniti logo creatives, 2026-06-11)**:
   the watcher arms an IntersectionObserver on the injected element — ≥50%
   in view for 1 continuous second (timer cancels if it leaves view), fires
   once per pageview (`window.__nwSponsorViewable`). On fire it pushes
   `{event: 'nw_sponsor_logo_viewable'}` to `dataLayer` (inert until wired
   in GTM/GA4) and requests `cfg.viewUrl` if set — **put the agency's DCM
   viewable-impression tracker there when they provide one**. Verification
   marker: `#nw-sponsor-logo-viewed` appears in the DOM on fire.
2. **Carrier-reposition (LIVE on the Infiniti logo, 2026-06-11 evening;
   un-clip fix 2026-06-22)** — makes GAM Active View honest: the watcher
   glues the carrier slot div (whose iframe GAM measures) onto the injected
   element with `position:fixed`, rAF-synced on scroll/resize plus img-load
   and delayed re-syncs. CSS-only, so the GPT iframe never detaches/reloads;
   transparent + `pointer-events:none`, so clicks pass through to the
   content. Verified: carrier and strip geometry identical at rest and
   while scrolling off-viewport.
   **Size the carrier to the iframe's REAL height, never the visible
   element's.** GPT forces the out-of-page iframe to a fixed height
   (**~150px** for this slot) regardless of a 1x1 placeholder. Active View
   measures that iframe element and **respects ancestor `overflow:hidden`
   clipping** (it's IntersectionObserver-based), so a carrier clipped to the
   logo's ~24px leaves only ~16% of the iframe in view — under the 50% bar —
   and books every impression **not-viewable**. This is exactly what bit the
   sponsor logo: it ran ~1% viewable / mostly 0% measurable for weeks despite
   the glue working, until the carrier was sized to the iframe's full height
   (`overflow:visible`, `height = max(iframe.offsetHeight, …)`). Confirmed
   live 2026-06-22 by reading the iframe's IntersectionObserver ratio:
   **0.16 clipped → 1.00 un-clipped**. The carrier stays transparent +
   `pointer-events:none`, so the oversized (but empty) carrier is invisible;
   AV then credits the in-view geometry where the logo actually sits. (The
   incumbent-era creatives measured 0% measurable — older render path; this
   stack's friendly-iframe render is measurable.)
3. **Full reposition variant (build before selling banner-size
   injections)**: same idea but the GPT iframe IS the visible ad —
   position the carrier over a spacer at the target location instead of
   writing a separate iframe. Active View native, no beacon needed.

**You cannot self-test Active View by throwing traffic at the page.**
Automated/headless/datacenter loads are IVT-filtered out of AV's `measurable`
+`viewable` counts, so they read **0 regardless of whether the carrier
geometry is right** — and on a live flight they're invalid traffic that
pollutes the buyer's metrics (and trips any DV/IVT exclusion on the LI). The
only valid AV read comes from **organic human impressions** (next-day, AV lags
~1 day). For pre-data confidence, measure the carrier iframe's
**IntersectionObserver in-view ratio** on the live served creative — that's the
exact geometry AV computes, and it's how the 2026-06-22 un-clip was validated
(0.16 clipped → 1.00 un-clipped) across mobile/tablet/desktop.

## Worked example: Apple FITO top banner (2026-06-11)

`scripts/setup_fito_top_banner.py` (dry-run by default, lookup-first).
The agency's 970x250 Innovid tag rendered **between the article title and
the video player** on the World Cup weather test article:

| Object | Id | Notes |
|---|---|---|
| Line item | 7337440033 | `[TEST] Apple FITO - top banner (video/title) relocation`, p3, inarticle1, KV `nwdemocr=06907703`, 970x250 only |
| Creative | 138562424408 | Wrapper embedding the Innovid tag; anchors on `[class*="VideoPlayer"][class*="__container"]` (fallback `mux-player#nw-video-player`), full-width breakout above it |

The Apple takeover LI (7334824462) keeps serving every other slot —
no agency objects modified. Verified live: title (y≈212) → ad (y≈446) →
player (y≈728); Innovid stack loaded inside the wrapper iframe; carrier
slot hidden; mobile unaffected.

## Gotchas (each cost real time)

- **New LIs on an approved order sit INACTIVE** until the order is
  re-approved — UI-only: the service account gets `PERMISSION_DENIED` on
  `ApproveOrders` (and `ActivateLineItems` is NOT_ALLOWED for that state).
  One click in the UI, then **~10 minutes** before the new LI starts
  winning auctions.
- **"LI not winning" debugging order**: check `window.innerWidth` first —
  a narrow window gets the mobile slot config and a desktop-size-only LI is
  simply ineligible. Then check the slot's
  `getResponseInformation().lineItemId`.
- **Roadblocking ONE_OR_MORE** on any LI that targets multiple units —
  ONLY_ONE lets a ghost serving (e.g. unrenderable oop2) consume the
  pageview and block the renderable slot (this launched the Infiniti
  flight dark; see the sponsor-logo doc).
- Anchors are hashed CSS-module classes — match on stable substrings
  (`[class*="VideoPlayer"][class*="__container"]`) and expect a frontend
  redesign to silently break them; verify after site releases.

## Lifecycle

Demo placements live on Newsweek_Test-2 (4082002976), gated on `nwdemocr`
values, so real traffic never sees them; pausing the LI kills one
instantly. A sellable flight is the same two objects on the sales order
with real targeting (e.g. `article_id`) instead of the demo KV.
