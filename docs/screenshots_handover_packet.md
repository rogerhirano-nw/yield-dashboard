# Proof-of-placement screenshots: handover packet

A self-contained brief on producing the client **proof-of-placement
screenshots deck** for a Direct campaign, written so a new Claude account (or
a new person) can do it cold. Load it as Project knowledge, or give the new
account access to this repo.

Compiled 2026-10-01 from `docs/screenshots_document.md` (the full runbook,
which is newer if they ever disagree), `scripts/pull_screenshots_source.py`,
`scripts/capture_screenshots.py`, `scripts/frame_device_shot.py` and
`scripts/build_screenshots_deck.py`.

---

## 1. The job in one line

**Someone sends a GAM order id (or line item id), and you send back
`<Advertiser> - Proof of Placement.pptx`.** Always a `.pptx` (Roger,
2026-09-23: "the output must be always in powerpoint"). Raw PNGs or a browser
deck aren't the deliverable. A claude.ai Slides artifact is fine as a
preview only.

## 2. The six steps

GAM credentials are repo Actions secrets, so the pull and the capture run as
workflows. From a cloud session without `gh`, dispatch with the GitHub MCP
tools and fetch artifacts through the REST API (`GET …/actions/runs/<id>/artifacts`,
then `…/artifacts/<id>/zip`).

1. **Pull.** Dispatch `pull_screenshots_source.yml` with `-f order=<id>` (every
   line) or `-f line_item=<id>` (a line resolves its own order; GAM deep links
   carry the LI). It's read-only. Artifacts:
   - `screenshots_source.json`: raw facts
   - `screenshots_doc.md`: the internal working doc

   Read off the advertiser, the **vertical (token 2 of the order name)**,
   LI ids, flight, goal, sizes, IO, creative count, and the **seller** (the
   last name token resolved through `settings.json → ae_names`, e.g.
   `AShah` → "Amit Shah"). A line with **no creatives** has nothing to shoot,
   so stop and say so.
2. **Pick the page.** Choose a live newsweek.com article that passes **both**
   tests in §3.
3. **Capture.** Dispatch `capture_screenshots.yml` with
   `-f line_item=<id> -f article_url=<page>`, **once per line item**. It:
   - gets each creative's preview URL (SOAP `getPreviewUrl`)
   - loads the article in headless Chromium at **desktop 1600px** and
     **mobile 390px**
   - scrolls the lazy slot into view
   - re-checks both page tests before shooting (`-f no_gate=true` is the
     deliberate override)

   It returns, per creative per viewport, `_context`, `_crop` and `_framed`
   PNGs.
4. **Look at every `_context` shot yourself.** Check that the **client's**
   creative is in the slot (not a Newsweek house ad) and that no consent
   banner covers it. `_crop` exists only for that legibility check and
   **never goes in the deck**.
5. **Build.** Write a spec like
   `docs/snippets/screenshots_deck_spec.example.json`. Point each shot at its
   **`_framed`** file, set `captured` = capture time in ET, and set `seller`.
   Then run:
   ```bash
   python scripts/build_screenshots_deck.py spec.json "<Advertiser> - Proof of Placement.pptx"
   ```
   (needs `python-pptx` + Pillow).
6. **Send** the `.pptx` and add a row to the *Instances* table in
   `docs/screenshots_document.md`.

## 3. Picking the page: two tests, both required

**In the client's industry.** Map the vertical (token 2) to a category slug
(`_VERTICAL_SLUGS` in the pull script). The page's **own** key-value
`cat`/`sitecat` must be `nwus-<slug>` (hyphens as underscores, e.g.
`nwus-health`). **The section listing lies.** An article under /health read
`cat=nwus-family_parenting`. Only the page's KV counts.

**Brand safe.** Read it off the page; don't judge by the headline:

| Key-value | Passes | Fails |
|---|---|---|
| `brandsafe` | `y` | `n` |
| `adexclusion` | no label containing `brand_safety` | e.g. `generic_brand_safety` |

- `adexclusion` is a **label list, not a verdict**. Other labels such as
  `nopassfq` (DoubleVerify flagging the headless runner) are logged and
  **don't block** (#384).
- `ABS` / `CBS` / `BSC` and Proximic `vnd_prx_segments` are opaque segment
  ids, not pass/fail.
- On-topic but grim (malpractice, outbreak, death) is the classic trap. On
  21 Sep 2026, two of four /health articles were in the right vertical and
  still flagged `brandsafe=n`.
- A failing page gets **skipped, not cropped around**. Re-check on the day,
  because `adexclusion` changes under a URL.
- For a **run-of-site** line, the page is a presentation choice. The doc says
  so, so the shot doesn't imply contextual targeting that was never bought.

Known-good health pages (both `cat=nwus-health`, `brandsafe=y` when last
checked):
- `newsweek.com/scientists-find-potential-way-to-preserve-muscle-during-glp-1-weight-loss-12468110`
- `newsweek.com/could-diet-reduce-alzheimers-risk-what-experts-say-12449451`

## 4. Capture gotchas

- **A creative is only shot on a viewport it fits.** A 970x250 in a 390px
  mobile slot serves a **Newsweek house ad**, so the "proof" would show the
  wrong advertiser (seen 2026-09-21). The script skips it. A 970x250 gets a
  desktop shot only.
- **Article ad slots are lazily defined**, so a shot at page load catches an
  empty well. The script scrolls to the slot first.
- **The Ketch consent overlay is hidden, never clicked.** Hiding isn't
  consenting.
- `preview_mobkoi_dom.yml` is **not** a capture tool. It's mobile-only DOM
  forensics, and a 970x250 falls through to a house ad there.
- GAM end times are ET instants. A line ending 13 Oct 23:59 ET reads 14 Oct
  in UTC.
- An advertiser named `[nw] …` is excluded from the dashboard's Direct table,
  so the campaign won't show there while it runs. The doc flags it.

## 5. Deck rules (all owner decisions)

- **Order:** cover → campaign summary → **one slide per in-context shot** →
  **a closing slide naming the seller** (Roger, 24 Sep 2026). Remove any
  internal status slide before it goes to the client.
- **In-context only, no close crops** (Roger, 22 Sep 2026). A crop is a
  picture of an asset the client already has.
- **Every shot sits in a device frame**: iPhone for mobile, MacBook Pro for
  desktop (`scripts/frame_device_shot.py`, auto by orientation, override with
  `--device phone|laptop`). **The frame never covers captured pixels**: no
  notch, status bar or menu bar painted over it, and the unframed original is
  kept. That's why the laptop has a camera dot instead of a notch.
- Shots are scaled to fit (`contain`), **never cropped** (`cover`).
- **One deck per advertiser**, even when two flights share an event.
- Captions carry the URL, capture time (ET) and size.
- Look: Newsweek "Paper" (paper `#FEFCF6`, ink `#1F1E19`, brand red
  `#E91D0C` as chrome only). The `.pptx` uses Cambria/Calibri so it renders
  on the client's machine.
- **Count slides from what the capture actually produced.** Two sizes (970x250
  + 300x250) give three slides: 970 desktop, 300 desktop, 300 mobile. Cut
  slides that weren't produced rather than leaving "awaiting capture"
  placeholders.

## 6. Past instances (for reference)

| Campaign | Order | State |
|---|---|---|
| KFSHRC (AI Health Summit 2026) | 4198147401 | Shot in full, 3 slides (22 Sep) |
| American Hospital Dubai (AI Health Summit 2026) | 4194246183 | 1 of 7 shot |
| Elevance Health (AI Health Summit 2026) | 4202666637 | `.pptx` delivered, 3 framed shots + seller (23 Sep) |
| Becton Dickinson (AI Health Summit 2026) | 4202665578 | `.pptx` delivered, 3 framed shots + seller (23 Sep) |

Deck and working-doc artifact links are in `docs/screenshots_document.md` →
*Instances*. Those artifacts belong to the old claude.ai account, so a new
account may need them re-shared.
