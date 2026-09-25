# Section-hub sponsor lockup ("SPONSORED BY <logo>")

A centered **SPONSORED BY [logo]** lockup under the dek of a section hub
(the "tent pole" CategoryHub template, first used on **/ai-politics** for Kia),
served entirely from GAM through the out-of-page unit **`oop1`**.

| Piece | Where |
|---|---|
| Creative (paste into GAM) | `docs/snippets/section_sponsor_lockup_creative.html` |
| GAM setup (LI, KV, creative push, LICA) | `scripts/setup_section_sponsor_lockup.py` → `.github/workflows/setup_section_sponsor_lockup.yml` |
| Render + placement check (local replica or real page) | `scripts/preview_section_sponsor_lockup.py` |

## What the page gives us (probed on qa.next.newsweek.com/ai-politics, 2026-09-25)

```html
<header class="CategoryHubHeader-module-scss-module__…__container">   <!-- flex column, centered -->
  <svg/>  <h1>AI Politics</h1>
  <div class="…__descriptionContainer">                               <!-- flex column, gap 32px (24px mobile) -->
    <p class="…__description">AI Politics covers …</p>
    <div id="dfp-ad-oop1" class="dfp-tag-wrapper"> … GPT iframe … </div>
  </div>
</header>
```

- **The oop1 slot already sits exactly where the lockup goes**: inside the
  header, directly under the dek, with the container's own 32px/24px gap
  above it. Engineering renders it in the client tree, so hydration keeps
  it (unlike the article oop divs; see `docs/article_sponsor_logo.md`).
- Page key-values: **`page_type=categories`** and **`categories=<slug>`**
  (`ai-politics`; also `cat`/`sitecat=nwus-ai_politics`). `categories` is the
  per-hub scope, so no creative-side URL gating is needed.
- Fonts: the UI sans is **Noto Sans** (600, uppercase for kickers, ink
  `#1f1e19`), headings Playfair Display, body Noto Serif.
- The QA hub already scrolls horizontally at ≤768px from its own content
  carousel (`slick-track`, 869px wide at a 768px viewport). That's a
  pre-existing template bug, not the lockup. The preview compares against it.

## How the creative works: in-frame, not injected

Because the slot is already in the right place, the creative **draws the
lockup inside its own GPT iframe** and resizes that (friendly, SafeFrame-off)
iframe from 1×1 to full container width × lockup height. There is no
parent-DOM injection, watcher, or carrier glue. This is the one approach
where **Active View measures the real ad**: the measured iframe *is* the
lockup. The article logo needed the carrier-reposition hack precisely
because it rendered outside its iframe. Clicks go through
`%%CLICK_URL_UNESC%%%%DEST_URL%%`.

- **Scope guard:** it renders only when its iframe sits inside
  `[class*="CategoryHubHeader"]`. Anywhere else the slot collapses and the
  logo is never requested.
- **Impressions:** the logo loads through `%%VIEW_URL_UNESC%%`, so the
  out-of-page impression counts when the logo actually loads. A guarded
  (off-template) render never counts.
- **Fonts:** an iframe can't see the page's web fonts, so the creative copies
  the parent's Noto Sans `@font-face` rules (URLs absolutized) and flips
  their `font-display: optional` → `swap`. With `optional` the fresh iframe
  painted the fallback Arial; verified Noto loads with `swap`.
- **Sizing:** label 14px / logo 28px tall (12px / 24px when the container
  is ≤480px wide, i.e. phones), 12px gap, centered. It refits on logo load,
  font load and resize.
- Agency impression pixels go in `CFG.pixels` (fired once per render). Don't
  graft them into the code by hand. Declare the pixel vendor's ad technology
  on the creative (see CLAUDE.md "GAM facts").
- **Hosted-image `alt`** = `CFG.sponsor`.

## Trafficking

1. **Dry run** (any push to the branch, or dispatch without `apply`): reports
   the oop1 unit, whether `categories=<slug>` and the `nwdemocr` value exist,
   and **every other active line item on oop1**.
2. **Create the [TEST] line**: dispatch with `apply=true`. You get a
   Sponsorship, priority 3, on Newsweek_Test-2, targeting oop1 +
   `categories=<slug>` + `nwdemocr=<demo value>`, with an "Out of page"
   placeholder.
3. **Add the creative from that line in the GAM UI**: size **Out of page**,
   **SafeFrame off**, upload the logo as asset **PNG1** (transparent PNG,
   ~3× the 28px display height), set the click-through URL, and paste the
   snippet the dry run prints. (A 1x1 CustomCreative made through the API
   doesn't serve an out-of-page slot.)
4. Dispatch again with `creative_id=<id>` and `apply=true`. The script
   rewrites the snippet from the repo (CFG filled), forces SafeFrame off, and
   LICAs it. Re-run after any snippet edit.
5. Approve the order in the UI (the service account can't). Then check
   `https://qa.next.newsweek.com/ai-politics?nwdemocr=section-sponsor`.
6. **Real flight:** clone onto the sales order with real dates, drop the
   `nwdemocr` criterion, and keep `categories=<slug>`.

**Competing oop1 lines.** Any line on oop1 without page-KV targeting can win
the hub's oop1 impression and render nothing there (e.g. a breadcrumb-only
article logo). The dry run lists them all. On 2026-09-25 there were two, both
priority 4 and both KV-targeted (7248272621 "ResponsiveAds: Cinematic
Background" and Infiniti Newsmakers 7394329898), so neither conflicts. If an
untargeted one appears, outrank it or add `page_type IS NOT categories` to it.

## Verifying

```bash
python3 scripts/preview_section_sponsor_lockup.py --logo kia.png            # local replica
NW_QA_AUTH='user:pass' python3 scripts/preview_section_sponsor_lockup.py \
    --logo kia.png --url https://qa.next.newsweek.com/ai-politics           # the real page
```

It writes the creative into the page's own `#dfp-ad-oop1` iframe, creating
one if the slot came back unfilled, the way GPT would. It screenshots
1280/768/390 and checks the following: centered under the dek, the GPT
iframe fitted to the lockup, no added horizontal scroll, Noto Sans loaded,
and the off-template guard. QA basic-auth comes from the env only; never
commit it. Behind an egress proxy, set `NW_PROXY` and `NW_TRUST_SPKI` (the
proxy CA's SPKI hash).

Status 2026-09-25: passes at all three widths on the real QA hub with the Kia
logo. On Newsweek_Test-2: **[TEST] LI 7439607552** (oop1 +
`categories=ai-politics` value 453960343890 + `nwdemocr=section-sponsor` value
453960345576) and **creative 138613798793**, LICA'd. The creative was made via
the API with `--create-creative`. It mirrors the serving UI-made OOP creative
138562255517, which reads back as **CustomCreative 1x1, `isInterstitial=True`**,
SafeFrame off. `isInterstitial` is the field that makes an API-made 1x1 an "Out
of page" creative. The logo asset is a traced PNG
(`scripts/orders/assets/kia_logo_trace.png`); swap in Kia's original file in the
UI before a real flight. Left: approve the order (the service account can't).
