# Newsletter native ads (The Bulletin)

How the Newsweek **Bulletin** newsletter (Beehiv) serves GAM native ads, and the
hard-won gotchas from the Infiniti Newsmakers "Sponsored Content" build-out
(#261, 2026-06-15). If you touch a newsletter native slot, read this first.

## Architecture

- Beehiv embeds GAM ads as **email image tags**:
  `<a href="…/gampad/jump?…"><img src="…/gampad/ad?iu=<unit>&sz=WxH&…"></a>`.
  GAM renders the ad **server-side into a flat PNG** and returns it at that URL —
  an `<img src>` can only be an image, so the ad's headline/body/CTA are
  **rasterized pixels, not live HTML**.
- One **fluid (1×1, native-eligible) `TemplateCreative`** (`138562096121`) on one
  line item (`7335266347`) serves **every size**. The rendered layout comes from
  the **native style** matched on `(size, creative template, targeting)`. So
  **adding a slot size = add a native style at that size + point the Beehiv tag
  at it — no new creative / LI / LICA.**
- Ad unit: `/22541732127/newsletter.newsweek/the-bulletin`.
- Per-slot native styles (creative templates `12544544` Top Logo / `12543656`
  Bottom Banner / `12544547` Sponsored Content):
  - **Top Logo 600×80** — `972438`
  - **Bottom Banner** — live: **`996986` 600×250** (the 300×250 banner image
    centered in a 600-wide frame, see gotcha 8); earlier left-aligned
    `972441` 300×250.
  - **Sponsored Content** — live: **`977578` 600×720**; superseded earlier
    iterations: `972672` 600×560, `977473` 600×314.

## Gotchas (each cost a real debugging loop)

1. **Wrong ad-unit path → blank fill.** Beehiv had the Sponsored Content / Bottom
   Banner tags pointed at `/22541732127/the-bulletin` — a *different, empty* unit
   — while the Top Logo correctly used the full
   `/22541732127/newsletter.newsweek/the-bulletin`. The wrong unit no-fills →
   blank. (The 600×80 working while the others were blank was the tell.)
2. **Beehiv generates its own ad tags.** The *delivered* email's tags carried
   `esp=beehiiv`, `pubads` (not `securepubads`), Beehiv's own `clkk`
   (`{{subscription_uuid}}`), and a beehiiv.com `url` — Beehiv's ad system
   **replaced the pasted custom HTML at send**. The editor HTML is not what
   ships; trust the delivered email.
3. **Preview ≠ delivered.** Beehiv preview (and JSBin tests) render *live* and
   look fine; the real send is the rasterized image fetched by the mail client.
   The wrong-iu / blank / cached-image issues only surface in a delivered email.
4. **The image scales down on mobile; live text doesn't.** The mail client
   shrinks the 600px ad image to ~the screen width (~0.6×), so 16px baked text
   renders ~10px while the page's live 16px text stays 16px → the ad looks "too
   small." Fix: size native fonts **~1.6×** the page's (here headline 36 / body
   26 vs page ~22 / 16) and grow the frame to fit (→ 600×720).
5. **Match the background by DOM ancestry, not colour frequency.** The ad
   `<img>` sits in a `<td background-color:#FFFCF2>` and inherits it. The email
   carries several near-identical warm tones (`#FFFCF2`, `#FEFCF6`, `#F5EEE5`)
   that frequency-counting can't disambiguate — two guesses missed before parsing
   the DOM and climbing the ad's parent chain nailed **`#FFFCF2`**.
6. **The page's link CSS can't recolour the ad** (it's an image). The newsletter's
   global `a{color:#4B62E0;text-decoration:underline}` styles the ad's wrapper
   `<a>` (no visible effect — there's no text in it). Ad text colour is 100% the
   native style. In the Sponsored Content template the headline/body/CTA are each
   `<a class="sc-link">`, so colours are set **per section**: red title
   (`#e91d0c`) + red rule, black body, blue underlined CTA targeted via
   `.sc-content .sc-body:last-child a` (the CTA shares the `.sc-body` class, so
   it's the *last* one).
7. **Propagation lag ~6–9 min.** A native-style change takes minutes to reach the
   rendered image; testing sooner shows the *old* version — the single biggest
   source of "it's still wrong" confusion in this build.
8. **A sub-column-width banner reads left-aligned, not centered.** The newsletter
   column is ~600px; a 300×250 native style renders a 300px-wide image that GAM
   left-justifies in it. To center it, the **native style itself must be the
   column width** — clone to **600×250** (`996986`) and center the still-300px
   image inside: `.bt{width:600px}` + `.bt a{display:block}` + `.bt img{margin:0
   auto}`. Repoint the Beehiv tag to `sz=600x250` (same ad unit). The image is
   *not* stretched to 600 — it stays 300 and sits centered in the wider cream
   frame (same approach as the Top Logo's `margin:0 auto`).

## Verifying without GAM creds or a browser

Cloud sessions hold no GAM/agentmail creds and have no browser, so:

- **Delivered ad markup:** forward the test email to `newsweek@agentmail.to`;
  `scripts/inspect_inbox_email.py` (via `inspect_inbox.yml`) pulls it and dumps
  the ad `<a>/<img>` blocks, the resolved `sz`/`clkk`/`url`, whether the ad is
  `<img>` vs live HTML, and the **DOM ancestor background chain** behind each ad.
- **Rendered ad pixels:** fetch the live `gampad/ad?iu=…&sz=WxH&c=<cachebuster>`
  URL directly (it's public) — GAM returns the PNG. Open it / sample pixels to
  confirm colour and that a style change propagated. (Used throughout #261 to
  prove black-vs-blue text and exact background, e.g. corner pixel `(255,252,242)`
  == `#FFFCF2`.)

## Tooling

- `scripts/update_native_style.py` + `.github/workflows/update_native_style.yml`
  — read / patch / clone / restyle native styles via SOAP `NativeStyleService`:
  `--list`, `--create-from <id> --new-width --new-height` (clone at a new size,
  `+ --append-css-b64` to bake CSS in), `--set-background <hex>`,
  `--sc-text-color`, `--cta-color`, `--append-css-b64 <base64> --marker <name>`
  (arbitrary CSS block — base64 so `{`/`#`/`;` survive the shell),
  `--inspect-creative` / `--inspect-li`. Every CSS override is an **idempotent
  marker block** (`/* nw-<name>:start */ … /* nw-<name>:end */`) so re-runs
  replace rather than stack.
- `scripts/inspect_inbox_email.py` + `inspect_inbox.yml` — the delivered-email
  diagnostic above.
- Both run through Actions (no creds locally): a branch **push** runs a
  read-only dump to the PR; a `[native-style-apply]` commit marker (the workflow's
  `PUSH_APPLY_ARGS`) or a `workflow_dispatch` performs the write — same pattern as
  `archive_pli`. `GAMClient` methods: `list_native_styles` / `update_native_style`
  / `create_native_style_from` / `get_creative_detail`.

## To add or change a slot

1. `--create-from <nearest style>` at the new size (or `--set-background` /
   `--append-css-b64` to restyle in place). `update_native_style` is
   fetch-modify-write, so size/targeting/name are preserved.
2. Point the Beehiv tag at the new `sz=WxH` (and the `width`/`height` on the
   `<img>`); the fluid creative serves it automatically.
3. Wait ~6–9 min, then verify via a delivered forward and/or a direct
   `gampad/ad` fetch. Mind the mobile downscale (font sizing) and match the
   background to the newsletter canvas (DOM ancestry).

**Resize the content vs. resize the frame.** The native-style **size is the
`sz=WxH` the Beehiv tag requests** — GAM rasterizes at exactly that, and only a
style of that size+template serves it. So *growing the frame* (e.g. the Top Logo
600×80 → 600×96) **requires repointing the Beehiv tag**. To make an element
bigger *without* a tag change, enlarge it **inside the existing frame**: e.g. the
Top Logo's 600×80 was fully packed (logo `max-height:44px` + "Presented by" label
+ padding ≈ 80px), so to render the logo taller we tightened the chrome (`.pb`
padding 10→5, label margin 6→3) and raised the cap to `max-height:52px`
in place (`--append-css-b64 … --marker logoheight --style-ids 972438`) — same
600×80, tag untouched. Headroom is limited by the frame, so a bigger bump still
means growing the frame + the tag. (Aspect note: a wide logo capped by
`max-width:560px` in the 600 frame is width-bound — raising `max-height` only
enlarges a height-bound logo, which a small-looking 44px-in-80px logo is.)
