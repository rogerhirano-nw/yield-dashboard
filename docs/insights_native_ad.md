# Insights native ad — fixed-size banner styles

The **Insights** sponsored-content card (the Infiniti QX65 unit) is a GAM
**native** creative, not an HTML banner. This doc covers extending it to the
three standard display sizes — **970x250, 728x90, 300x250** — so the same
creative an AE already trafficked can also run as a banner.

## What already existed

| Object | Id | Notes |
|---|---|---|
| Creative template | `12412102` | "Insights Premium Spotlight", `USER_DEFINED`, native-eligible |
| Native style | `989975` | **fluid 1x1**, targeted at `homepage3` (23207094803) |
| Example creative | `138561753906` | "Native Insight Infiniti", `TemplateCreative`, size 1x1 |
| Example line item | `7331747232` | `Newsweek_Direct_Automotive_…_Infiniti-Qx65-2026-Native-Insight_…`, SPONSORSHIP, 1x1 placeholder |

Template variables (the buyer-facing fields an AE fills in):

| Variable | Type | Required | Used as |
|---|---|---|---|
| `TITLE` | String | ✅ | headline |
| `SUBTITLE` | String | | dek — **970x250 only** |
| `HASHTAG` | String | | `#Automotive` category tag |
| `IMAGE` | Asset | | hero photo |
| `LOGO` | Asset | | advertiser logo in the "Sponsored by" lockup |
| `3RDPARTYTRACKING1` / `2` | Url | | 1x1 pixels, rendered hidden |

The one existing style is **fluid**, so a native creative on this template can
only render where a fluid slot asks for it. Nothing let it fill a 970x250.

## What was added

Three fixed-size native styles on the same template, sharing **one markup and
one stylesheet**:

- `docs/snippets/insights_native_style.html` → `NativeStyle.htmlSnippet`
- `docs/snippets/insights_native_style.css` → `NativeStyle.cssSnippet`

A native style renders in an iframe sized to the slot, so the creative's
viewport **is** the creative size, and plain media queries pick the layout
exactly — no JS sizing, no detection to get wrong:

| Query | Layout |
|---|---|
| base (`<600px`) | **300x250** — stacked: header, hero, text (this is the reference card) |
| `≥600px` and `≤150px` tall | **728x90** — 16:9 hero left (160x90), story right |
| `≥860px` and `≥200px` tall | **970x250** — full-width header band, story left, hero right |

Because a native style is scoped to its **creative template**, targeting these
site-wide only changes how *Insights* creatives render. No other native
creative in a 300x250 slot is touched.

### Design rules carried over

Type, color and the red rule are lifted verbatim from style `989975`, so all
four surfaces read as one unit: Playfair Display 600 headlines, Noto Serif
body, `system-ui` for the uppercase labels, `#F8F4E8` paper, `#1f1e19` ink,
`#68645a` muted, and the 2px `#e91d0c` tick above the wordmark. The class
names match too (`insights-hero__*`), so a page audit or a GAM UI diff reads
the same across sizes.

The banners add one thing the fluid unit doesn't have: an **always-on 1px
border** (`--rs-color-border-neutral-faded`, the warm hairline already declared
in `989975`). The paper ground is close enough to a white page that the unit
otherwise bleeds into the article; the hairline is what makes it read as a
discrete card. `box-sizing` is `border-box`, so it costs 2px of the grid —
the rectangle's `--media-h` and the leaderboard's paddings are set short to
pay for it, which is why 728x90 in particular has no slack left.

Two deliberate deviations, both forced by the height budget:

1. **The hero is cropped** (2.7:1 / 16:9 / 2.2:1) rather than always 16:9.
   `object-fit: cover`, centered — supply hero assets with the subject
   centered and headroom to spare.
2. **The dek (`SUBTITLE`) shows on 970x250 only.** 728x90 and 300x250 end at
   the headline, which is the identity. Disclosure never depends on it: the
   **"Sponsored by &lt;logo&gt;" lockup and the "SPONSORED" tag both run at all
   three sizes**.

### Hero asset requirements

The unit renders its own headline, so the hero is a **photograph, not a
poster**. Two rules, both learned the hard way on the Cognizant sample:

- **Landscape, ≥1200px wide, subject centered.** The hero is cropped per size
  (2.7:1 / 16:9 / 2.2:1) with `object-fit: cover`, centered. **16:9 is the
  safe master** — it is the leaderboard's native ratio and crops cleanly into
  the other two.
- **No burned-in copy.** A square social asset with a headline baked into it
  (the Cognizant `…1200x1200…` file carries "Building the bridge to AI impact"
  across its top-left) gets sliced through that text at every size, because the
  crop is a centered band. There is no per-creative `object-position` escape —
  a native style's CSS is shared by every creative on the template — so the fix
  is the asset, not the style. Re-crop to a clean landscape region before
  uploading.

### Copy limits

Every size clamps its headline (2 lines, 3 on the rectangle) and the 970's dek
(3 lines), so **over-long copy fails as an ellipsis, not as a broken box**.
The Infiniti creative — an 84-char `TITLE` and a 166-char `SUBTITLE` — is the
practical ceiling: it fills every clamp exactly with no truncation. Longer copy
will ellipse. Check before a flight goes live:

```bash
python3 scripts/preview_insights_native.py --creative-id <id>
```

For copy that **isn't trafficked yet** — proofing a headline and hero before
the creative exists — both QA tools also take a values file, which is when an
over-long title is cheapest to fix:

```bash
python3 scripts/preview_insights_native.py    --values-json vals.json --prefix cognizant
python3 scripts/build_insights_test_pages.py  --values-json vals.json --prefix cognizant
```

```json
{
  "TITLE": "...", "SUBTITLE": "...", "HASHTAG": "Technology",
  "IMAGE": "<url | local path | data: uri>",
  "LOGO":  "<url | local path | data: uri>",
  "DEST":  "https://www.newsweek.com/insights/..."
}
```

`IMAGE`/`LOGO` are inlined as data URIs, so a URL, a local file and an already
-inlined asset all work the same way. `--prefix` keeps one advertiser's output
from overwriting another's.

It pulls that creative's real values from GAM, renders all three sizes in
headless Chromium at exact pixel size, writes PNGs to
`data/insights_preview/` (gitignored), and flags anything that overflows or
clips (`fits` is the good answer). This is local design QA — no GAM write, no
on-site preview needed.

### Test page

```bash
python3 scripts/build_insights_test_pages.py --creative-id <id>
```

Writes `data/insights_preview/insights_test_page.html` — **one self-contained
file** (assets inlined as data URIs, no network beyond the webfonts) to open
locally, email, or send to a seller. Two views: the three units dropped into a
mock article shell (billboard above the masthead, leaderboard mid-article,
rectangle in the rail) so they can be judged next to editorial, and each unit
isolated at 1:1 for pixel review.

Every unit sits in an **iframe sized to exactly its slot**, which is the same
box GAM hands a native style — so the media query that picks the layout
resolves in this file exactly as it will on-page. What fits here fits in the
slot.

The mock shell is placeholder text under an "Ad QA Harness / not a live page"
masthead. It stands in for slot geometry; it is not a reproduction of a
Newsweek page and shouldn't be presented as one.

**Both tools drop the `3RDPARTYTRACKING1/2` pixels rather than substituting
them.** Those are real advertiser/measurement URLs (ml314 on the Infiniti
creative) and a QA render is not an impression — firing them would put design
previews into the buyer's counts. Nothing visual depends on them; they render
hidden.

## Trafficking

```bash
python3 scripts/setup_insights_native_styles.py                    # dry run
python3 scripts/setup_insights_native_styles.py --apply            # create
python3 scripts/setup_insights_native_styles.py --apply --update   # push CSS edits
python3 scripts/setup_insights_native_styles.py --apply --ad-unit 23207094803
```

Lookup-first by name, so it is safe to re-run after a partial apply. Default
targeting is the **`newsweek` site root** (23207092721, `includeDescendants`);
narrow it with `--ad-unit` for a pilot. Edit the snippet files and re-run with
`--update` to push style changes — the styles are the deploy surface, so a
CSS fix reaches every live Insights banner without touching a creative.

**One manual step is not automated:** add 970x250 / 728x90 / 300x250 to the
line item's **creative placeholders** in the GAM UI. The creative itself stays
1x1 native; the placeholders are what make it eligible for those slots.

## Gotchas

- **The styles are fixed-size only.** The stylesheet sets `html, body { height:
  100% }` so the grid fills a known box. Do not paste it into the fluid
  in-article style (`989975`) — there the unit would take the viewport height.
- **`@import` pulls Playfair Display + Noto Serif from Google Fonts**, same as
  the incumbent style. If the fonts are blocked the stack falls back to
  Georgia, which is close but sets wider — the clamps absorb it.
- **`%%CLICK_URL_UNESC%%%%DEST_URL%%` with `target="_top"`** is carried over
  from `989975`; the click-through comes from the creative's `destinationUrl`.
- The two tracking pixels render inside hidden `<div>`s. An unfilled
  `3RDPARTYTRACKING2` yields an `<img src="">`, which is what the incumbent
  style already does — harmless, and left as-is for parity.
