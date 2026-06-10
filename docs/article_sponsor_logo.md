# Article-page sponsor logo (served from GAM via oop2)

A "Presented by <logo>" strip at the **right of the article breadcrumb row**
(`Autos | Volvo | Safety ............ Presented by [logo]`), sold as a
sponsorship and served entirely through GAM — flights, impression counting,
click tracking, and creative swaps all happen in ad ops with **no
newsweek.com template change**.

It rides the existing out-of-page unit **`oop2`**
(`/22541732127/newsweek/oop2`, ad unit id 23207098418), which is already on
every article page, eager-loaded (`lazy:false`). The creative is an
out-of-page CustomCreative whose JS injects the strip into the page DOM.

`scripts/setup_article_sponsor_logo.py` creates the GAM objects. Dry-run by
default, `--apply` to create, safe to re-run (lookup-first by name).

## GAM objects

| Object | Id | Name | Notes |
|---|---|---|---|
| Line item | 7336410928 | `[TEST] Article Sponsor Logo - oop2` | SPONSORSHIP / CPD $0 / 100% daily, on Newsweek_Test-2 (4082002976), targets oop2 |
| Creative | 138563009050 | `[TEST] Article Sponsor Logo - oop2` | CustomCreative 1x1, **SafeFrame OFF**, inline SVG placeholder logo |

Created 2026-06-10 (`--apply` run). The test LI is unapproved — approving it
in the GAM UI is the on-site preview switch. Injection verified against the
live article template the same day (desktop 1440px: strip absolute-right in
the breadcrumb row; mobile <768px: static fallback, right-aligned in the
row's flex flow).

## How the creative works

- GPT renders oop2 via `defineOutOfPageSlot`; with
  `isSafeFrameCompatible: false` the creative gets a **friendly (same-origin)
  iframe**, so its JS can reach `window.top.document`. SafeFrame on = the
  injection silently no-ops — never flip that flag on this creative.
- The JS looks for the breadcrumb container by the stable parts of its hashed
  CSS-module class: `[class*="ResponsiveBreadcrumbs"][class*="__container"]`.
  Found → inject the strip absolutely positioned at the row's right edge
  (below 768px it drops to a right-aligned row under the breadcrumbs).
  Not found → render nothing. That makes the creative **self-scoping to
  article pages** even though oop2 may exist on other templates.
- Click-through uses `%%CLICK_URL_UNESC%%%%DEST_URL%%`, so GAM click tracking
  works and the destination URL is managed on the creative.
- Disclosure label ("Presented by") is part of the snippet — change there if
  sales needs "Sponsored by" / "In partnership with".

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
