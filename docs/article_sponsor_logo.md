# Article-page sponsor logo (served from GAM via oop2)

A "Presented by <logo>" strip at the **right of the article breadcrumb row**
(`Autos | Volvo | Safety ............ Presented by [logo]`), sold as a
sponsorship and served entirely through GAM.

**Hard product constraints (Roger, 2026-06-11): the solution is isolated to
the out-of-page unit `oop2`** (`/22541732127/newsweek/oop2`, ad unit
23207098418) — no page-side changes of any kind, and no other ad unit may
be involved in any role. (A bootstrap creative riding inarticle1 to bind
oop2's missing div was built, verified, and then **rolled back** on this
instruction — PR #166, reverted. The bootstrap creative 138562352639 is
deactivated/unassociated; safe to archive.)

The creative is an out-of-page CustomCreative whose JS injects the strip
into the page DOM.

## Final architecture (2026-06-11): two OOP-family carriers, one LI

The line item targets **oop2 (23207098418) + interstitial (23295929518)**;
creative sizes route each request. Coverage, all verified live:

| Template | Carrier | Logo appears |
|---|---|---|
| Next.js articles (both generations) | `interstitial` (2x1 creative) | on the reader's **first scroll**, then persists (incl. back at top) |
| Sections that bind oop2 (e.g. `/ai`) | `oop2` (out-of-page creative) | at load, centered inside the oop2 div (`sl-indiv` mode, incumbent-style) |

Why two carriers — the structural difference on article pages:
- The eager **oop divs** (`fetchpriority=high`, bare, wrapperless) exist
  only in the server HTML; the client tree doesn't render that branch, so
  hydration deletes them and the wrapper's eager `display('dfp-ad-oop2')`
  always fails. oop1/oop3 aren't even defined on article templates.
- The **interstitial div** uses the lazy two-div wrapper pattern
  (`dfp-ad-lazy dfp-ad-count`, like `inarticle*`), which IS client-rendered
  → survives hydration on every article template. Their wrapper displays it
  via IntersectionObserver as the reader scrolls toward the article's end —
  hence first-scroll, not first-paint.

**First-paint on articles is not reachable from GAM** under the
no-page-changes constraint: the only first-paint mechanisms are the page
client-rendering its eager oop branch (their hydration bug) or a bootstrap
creative on an eagerly-rendered slot (built, verified, rolled back — see
below). First-scroll-then-permanent is the ceiling, and the sponsor logo
stays for the entire remainder of the read.

Coordination note: the `interstitial` unit currently has **zero demand**,
but if a real interstitial campaign ever launches, the same SOV-collision
rules apply as with the oop2 incumbent.

`scripts/setup_article_sponsor_logo.py` creates the GAM objects. Dry-run by
default, `--apply` to create, safe to re-run (lookup-first by name).

## GAM objects

| Object | Id | Name | Notes |
|---|---|---|---|
| Line item | 7336410928 | `[TEST] Article Sponsor Logo - oop2` | SPONSORSHIP / CPD $0 / 100% daily / priority 3, on Newsweek_Test-2 (4082002976), targets oop2 + interstitial + KV `nwdemocr=infiniti-logo`, placeholders 1x1-OOP + 2x1 |
| Creative (oop2) | 138563017162 | `[nw] Test_…_Out-of-page` | UI-created **"Out of page"** size, **SafeFrame OFF**, Infiniti logo asset (`%%FILE:PNG1%%`), dual-mode watcher (`sl-bc` breadcrumbs / `sl-indiv` in-div) |
| Creative (interstitial) | 138563124568 | `[TEST] … interstitial (2x1)` | **The article carrier.** Same watcher snippet, breadcrumb mode, logo via stable GAM CDN URL |
| Creative (bootstrap, retired) | 138562352639 | `[TEST] … oop2 bootstrap (300x250)` | inarticle1 div-binder — worked, rolled back per the no-other-banner-slots constraint. LICA deactivated; safe to archive |
| Creative (defunct) | 138563009050 | `[TEST] Article Sponsor Logo - oop2` | First attempt — API-created as plain 1x1, which GAM won't serve into an OOP slot. Unassociated; safe to archive |

**Out-of-page creatives must be "Out of page" size, not 1x1.** The API
equivalent on the line item is creative placeholder
`creativeSizeType: INTERSTITIAL`; the creative itself is most reliably added
from the LI in the UI with size "Out of page" (how 138563017162 was made).

The test LI is demo-gated: the site's `?nwdemocr=<value>` URL param sets a
GPT page-level key-value (watch for `[NWDEMOCR]` console logs), and the LI
targets `nwdemocr=infiniti-logo`, so it serves only on URLs carrying the
param. A real flight drops the KV.

**There is an incumbent sponsorship on oop2**: `Logo 120x60 AI`
(LI 6986067522, order 3648897741, DELIVERING — also a SPONSORSHIP at the
default priority 4, also a SafeFrame-off CustomCreative; it writes a
"presented by <logo>" strip *into* the `#dfp-ad-oop2` div itself). Two
consequences:

- It was splitting oop2 ~50/50 with the demo — one of the three causes of
  "the logo sometimes doesn't appear". The demo LI now runs **priority 3**,
  so it outranks the incumbent on `nwdemocr` URLs while real traffic is
  untouched (the demo is KV-gated). Real flights must coordinate with the
  incumbent instead: pause it, split by section/KV, or stack priorities
  deliberately.
- The incumbent renders inside the oop2 div, so hydration deleting that div
  (next section) wipes its strip mid-session; the watcher-based creative
  documented here survives that. The same hardening would benefit the
  incumbent if it renews.

GAM-side note: `updateLineItems` re-runs the forecast — flip
`skipInventoryCheck`/`allowOverbook` to `True` on the fetched object before
any update to an oop-targeted LI, or it throws `NOT_ENOUGH_INVENTORY`.

## How the creative works

- GPT renders oop2 via `defineOutOfPageSlot`; with
  `isSafeFrameCompatible: false` the creative gets a **friendly (same-origin)
  iframe**, so its JS can reach `window.top.document`. SafeFrame on = the
  injection silently no-ops — never flip that flag on this creative.
- The snippet does not inject directly: it plants a **watcher `<script>` into
  the parent document** (id `nw-sponsor-logo-boot`) and lets that do the
  work. Reason: Next.js hydration destroys the oop divs — and with them this
  creative's iframe — shortly after GPT renders them, and later re-renders
  can remove an already-injected strip. The parent-document watcher survives
  the iframe's death, injects as soon as the breadcrumb row exists, re-injects
  if a re-render removes the strip, and self-stops after ~2 minutes.
- The watcher finds the breadcrumb container by the stable parts of its
  hashed CSS-module class:
  `[class*="ResponsiveBreadcrumbs"][class*="__container"]`. Found → strip
  absolutely positioned at the row's right edge (below 768px it falls back
  into the row's flex flow, right-aligned). Not found → nothing renders,
  making the creative **self-scoping to article pages**.
- Click-through uses `%%CLICK_URL_UNESC%%%%DEST_URL%%`, so GAM click tracking
  works and the destination URL is managed on the creative.
- Disclosure label ("Presented by") is part of the snippet — change there if
  sales needs "Sponsored by" / "In partnership with".

## Known page-side bug: hydration removes the oop divs (2026-06-11)

> **Product constraint (Roger, 2026-06-11): everything is done through GAM —
> no page-side changes, and no other ad slots may carry the logo.** This
> section is kept as context, not as a plan: the bug caps the delivery rate
> of every oop campaign (including the incumbent `Logo 120x60 AI`), and the
> watcher creative recovers every render the page allows, but loads where
> the div never exists at display time are unreachable from GAM by
> construction — a creative only executes if GPT renders it somewhere.

**Symptom:** the logo appears only sometimes, varying by article and load.

**Root cause (verified in-browser on two articles):** article pages
server-render the `dfp-ad-oop1/2/3` containers (some older layout variants
don't render them at all), but the **Next.js client render does not include
them, so hydration removes them** — taking any already-rendered creative
iframe with them. The site's ad wrapper still calls
`googletag.display('dfp-ad-oop2')` and GPT logs, several times per page:

    [GPT] Error in googletag.display: could not find div with id
    "dfp-ad-oop2" in DOM for slot: /22541732127/newsweek/oop2.

GAM itself is fine — the slot's `getResponseInformation()` shows the
sponsorship LI/creative winning on every load. The ad just has no div to
render into by the time display runs. When the logo *did* appear, GPT's
eager display had won the race against hydration. **This very likely
no-ops every oop1/2/3 campaign on article pages** (skins, wallpapers,
anything out-of-page), not just the sponsor logo.

**Fix (web team, either works):**
1. Render the oop slot containers in the client component tree on all
   article layouts so hydration keeps them; or
2. In the ad wrapper, before calling `display()` on an oop slot, create the
   div if it's missing (out-of-page divs are position-independent):
   `if (!document.getElementById(id)) document.body.appendChild(Object.assign(document.createElement('div'), {id}))`.

**To reproduce / verify a fix** on any article page console:

    document.body.appendChild(Object.assign(document.createElement('div'), {id: 'dfp-ad-oop2'}));
    googletag.cmd.push(() => googletag.display('dfp-ad-oop2'));

With the test URL param (`?nwdemocr=infiniti-logo`) the Infiniti strip
appears in the breadcrumb row within ~1s. The creative-side watcher (above)
already handles the *other* half of the race — strips removed by re-renders
— but it can only run if the creative renders at least once, so the div fix
is required for reliable delivery.

## Trafficking a real sponsor flight

1. Clone the `[TEST]` LI onto the sales order (SPONSORSHIP, CPD, 100% daily
   goal, target oop2) and set real flight dates + rate.
2. Copy the creative, swap `LOGO_SRC` for the sponsor's logo (hosted URL or
   data URI; ~24px tall, transparent background) and set the real
   click-through URL. Keep SafeFrame off.
3. Scoping:
   - **All articles** — nothing more to do.
   - **One section only** (e.g. an Autos awards sponsor) — no `channel`/
     `pagetype` key-value exists in GAM today (only `bmb` audience
     segments), so either gate in the creative JS (check the breadcrumb's
     primary-category text/href, e.g. only inject when it links to
     `/autos`) or add a page-level KVP with the web team for clean
     GAM-side targeting + accurate impression counts.

## Caveats

- **Impressions count wherever oop2 serves**, including pages where the JS
  declines to inject. If oop2 exists on non-article templates, GAM numbers
  overcount the sponsorship; for billing-grade reporting scope the LI (KVP)
  or report on clicks/custom events. Verify what else runs on oop2 before
  selling 100% SOV — a competing oop2 campaign would collide with the
  sponsorship goal.
- The injection selector tracks the article template's CSS-module naming
  (`ResponsiveBreadcrumbs…__container`). A frontend redesign that renames
  that component silently kills the logo — worth a periodic check while a
  flight is live (screenshot or DOM probe of any article page).
- The service account **cannot create ad units** (`PERMISSION_DENIED` on
  `InventoryService.createAdUnits`) — fine here since oop2 already exists,
  but relevant if a dedicated unit is ever wanted: create it in the GAM UI,
  then scripts can target it.

## Learnings (native-style detour, kept for reference)

A first iteration used a new `sponsorlogo` ad unit + fluid native style on
the Top Logo format (12544544) with an on-page slot added by the web team.
Dropped in favor of oop2 (no web release needed). Two facts worth keeping:

- **Native style macros are `[%Var%]`, not `[Var]`.** The in-prod Insights
  Premium Spotlight style (989975) uses `[%LOGO%]`; the three newsletter
  styles (972438, 972441, 977473 — still INACTIVE) use bare `[Logo]` /
  `[AdImage]`, which GAM won't substitute. Fix before activating the
  newsletter campaign.
- Native styles are picked by **size + targeting** — keep styles targeted to
  their unit; untargeted styles on a shared format bleed across placements.
