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

### The card must separate from the page's ad band

The live page wraps ads in its own full-bleed warm **ADVERTISING** band, and the
unit's paper ground is close enough to it that a 12%-ink hairline vanished — the
card read as part of the page (Roger, on the live render, 2026-09-08). The
border is therefore a firmer warm rule (`--card-edge: #d5cdb6`) plus a low
shadow, so the unit reads as a discrete card on **cream and on white**.

`build_insights_test_pages.py` now reproduces that cream band, because the
original harness put the units on white and so could never have caught this.
**A QA harness that doesn't reproduce the host page's ad wrapper will miss
exactly this class of bug.**

### CTA

Every size carries a **READ MORE** button. On 970x250 and 300x250 it is pinned
to the bottom of the text column (`margin-top:auto`), so it sits on the card's
baseline no matter how many headline/dek lines a creative uses. **On 728x90 it
rides the meta row instead** — absolutely positioned top-right of the text
column — because 90px leaves no vertical room for a button under a two-line
headline; that placement costs zero height but reserves ~108px of width, which
is why the leaderboard's copy caps are tighter than the other two.

The CTA label is **hardcoded in the style markup**, not a template variable — so
changing it changes it for every creative on the template.

The banners also add something the fluid unit doesn't have: an **always-on 1px
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

### Fonts: the site's stack, not the dashboard's

`docs/design_handoff/` documents **Benton Modern Display + Franklin Gothic**.
That is the **dashboard's** system — an internal tool skinned with licensed
binaries. **newsweek.com serves something different:** a trending-bar link on
the live homepage inspects as **`12px "Noto Sans"`, `#1F1E19`** (2026-09-08).

The unit has to match the page it renders on, not the internal tool, so the
stack is **Noto Sans** (UI labels: SPONSORED BY, the category, the CTA),
**Playfair Display** (headline — what the incumbent style `989975` already
used), **Noto Serif** (body). All three are Google-hosted, which is also why a
cross-origin creative iframe can actually load them; Benton and Franklin are
licensed binaries the iframe could never reach.

**Don't "correct" these against `design_handoff/` again** — that was done once
on 2026-09-08 and had to be reverted. Verify against the live site with the
element inspector instead.

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

### Logo asset requirements — crop it to the mark

**The logo must be cropped tight to the wordmark, with no transparent
margin.** The stylesheet sizes it by *height* (`--logo-h`: 20px on 970x250,
14px on 728x90, 13px on 300x250) and lets width follow the aspect ratio, so
padding baked into the file shrinks the visible mark by exactly that
proportion.

Proven on the Cognizant sample (Roger, 2026-09-08: "the logo is appearing very
small"). The uploaded asset was a **3840x2160 logo-gallery export** — a 16:9
canvas with the wordmark floating in the middle band. Its ink filled 98% of the
width but only **31% of the height**, so at `--logo-h: 20px` the reader got
~6px of actual logo, rendered 35px wide next to an 88px "SPONSORED BY" label.
Re-cropped to its ink box (3780x691, 5.5:1) the same 20px renders **109px
wide** and the brand is legible at all three sizes.

No CSS can fix this — the padding is inside the image, and a native style's
rules are shared by every creative on the template, so there is no per-creative
`object-position` escape. Same conclusion as the hero: **the fix is the asset.**

`scripts/preview_insights_native.py` now canaries it. It measures the asset's
ink box in a canvas and prints one of:

```
  logo    -> ok (3780x691, mark fills 98% of height)
  logo    -> PADDED ASSET: mark fills 31% of the 3840x2160 file's height, so it
             renders 31% of --logo-h. Crop it to the mark.
```

Anything under 70% is flagged. Run the preview before a creative ships.

### The section-header rule is the site's, matched to source

The red tick over a hairline is the site's own section-header device (the one
above "Recommended For You" on the same article page). Two components ship it
identically in the page's CSS — `FeaturedWinners__divider` and
`RankingRelatedPosts__titleBar` — and both agree:

```
divider    display:flex; align-items:center; width:100%
red tick   width:24px;  height:2px;  #e91d0c   (--brand-default)
grey line  flex:1;      height:1px;  #e7e0c9   (--border-subtle)
gap below  16px, before a 28px Playfair title
```

**The tick is taller than the line and vertically centred on it**, so it stands
proud on both sides. The unit's first version drew one flat 2px bar with a red
segment at its left — same colours, but it read as a single heavy rule rather
than a tick over a hairline, which is what made it look unlike the page
(Roger, 2026-09-08). Verified in the render at 2×:

```
tick  : 24 x 2 css px, rgb(233,29,12), rows 38-41
line  : 1 css px,      rgb(231,224,201), rows 40-41   -> tick straddles the line
```

The tick is a **fixed 24px at every breakpoint on the site**, so `--tick-w` is a
single base token here too rather than a per-size override.

The one deliberate deviation is `--divider-mb`: the site allows 16px under the
rule before a 28px title, which a 250px-tall ad cannot spend. It runs 4–10px by
size.

### The card shadow has no y-offset

`box-shadow: 0 0 3px rgba(31,30,25,0.07)`, deliberately not the original
`0 1px 3px`. The downward offset was harmless while the card ran flush to the
iframe's last row — the shadow was simply clipped away. Once the card was inset
2px (below), that shadow rendered into the gap and the bottom edge read as a 1px
rule *plus* a soft smudge, heavier than the other three sides — "a 2 pixel border
at the bottom" (Roger, 2026-09-08). Sampled at 2x on the 970x250:

```
                        rows below the border (outside -> in)
  0 1px 3px             250, 248, 246, 244   <- visible smudge
  0 0 3px               253, 252, 250, 248   <- fades to page white
```

The border itself was always 1px on all four sides; only the shadow changed.

### Click-through opens in a new tab

The card's anchor is `target="_blank"` (Roger, 2026-09-08) — the sponsored
article opens in a new tab and the reader keeps their place in the article they
were reading. It was `target="_top"`, which replaced the host page.

`rel="noopener"` stays: without it the opened tab receives a `window.opener`
handle back into the ad document. **Do not add `noreferrer`** — it strips
`document.referrer` on the landing page, and advertiser-side analytics commonly
attribute on it. The click itself is tracked by Google's
`%%CLICK_URL_UNESC%%` redirect, not by the referrer, so GAM's click counting is
unaffected either way.

### The card must size itself in `vh`, never a percentage

**GAM does not serve the style's markup as a child of `<body>`.** It emits an
Active View container and then wraps the markup in a plain `<div>` that has no
height of its own — visible in any served creative:

```
<body><div class="GoogleActiveViewInnerContainer" …></div><script …></script>
      <div > …the style's markup… </div>
```

A percentage height against an auto-height parent is indeterminate, so the
card's `height: calc(100% - 2px)` silently fell back to **content height** and
the unit stopped filling its slot. Measured against a reproduction of that DOM
(2026-09-09):

| Size | `height: calc(100% - 2px)` | `height: calc(100vh - 2px)` |
|---|---|---|
| 970x250 | card **215px**, dek→CTA gap **0px** | 248px, 33px |
| 728x90 | card **92px — overflows its 90px box** | 88px, fits |
| 300x250 | 247px, 14px | 248px, 14px |

That single bug is what produced both live symptoms: on the billboard the
bottom-pinned CTA had no leftover space, so the button sat on the dek; on the
leaderboard the card ran 2px past its own slot. The rectangle happened to land
near 250px by content, which is why it looked nearly right and masked the cause.

**This also supersedes an earlier, wrong diagnosis.** The 300x250's missing
bottom border was attributed to the page's slot clipping the iframe's last pixel
row, with a recommendation to set `iframe { display: block }` on the ad slot.
That was wrong: at the time the rectangle's content ran past 250px, so the
content-sized card overflowed its own iframe and the border went with it.
Nothing needs changing on the page.

The `- 2px` is now precautionary headroom, not a fix for anything observed — it
guarantees the bottom border cannot be lost to an off-by-one in the slot, and
the rectangle pays for it out of `--text-pb`.

**The harness could not have caught this**, exactly like rendering onto white
instead of the page's cream ad band: it rendered the markup straight into
`<body>`, where `height:100%` resolves. `preview_insights_native.py` now wraps
every render in `_GAM_SHELL`, the real serving DOM. Reverting the CSS to `100%`
makes it report `cta gap only 0px` on the billboard and
`CARD EDGE ON THE CLIPPED ROW (clearance -2px)` on the leaderboard.

### The CTA needs reserved room on every size

All three layouts hit the same bug in three different ways: the button and the
copy collided because nothing reserved the space between them. Fixed
2026-09-08, and the fix is different per size because the CTA is positioned
differently:

| Size | CTA positioning | Gap before | after |
|---|---|---|---|
| 728x90 | `position: absolute` in the meta row | **-27px** (headline ran under it) | **14.5px** |
| 970x250 | in flow, `margin-top: auto` (pinned bottom) | **8.2px** at the dek's 3-line clamp | **14.6px** |
| 300x250 | in flow, `margin-top: var(--cta-mt)` | **6px** | **14px** |

- **728x90** — absolute means out of flow, so the button does *not* push the
  headline; the gutter is hand-reserved by `--cta-gutter`, which was 108px for a
  119.5px button. Now 150px = 120 (button) + 16 (`--pad-x`) + 14 (air).
- **970x250** — the button is pinned to the bottom, so the gap is simply
  whatever vertical space is left over. With a short dek that reads fine (28px),
  but as the dek grows toward its 3-line clamp the gap collapses to 8px. Room was
  bought back above it: `--text-pt` 8→4, `--hed-mb` 10→6, `--hed-lh` 1.3→1.25,
  and `--text-pb` 12→16 to lift the button off the card's bottom edge (13→17px).
- **300x250** — the gap *is* `--cta-mt`, which was 6px. The rectangle has **zero
  vertical slack** (10px already overflows by 4px), so the 8px was taken from the
  header rhythm — `--head-pt` 10→8, `--head-pb` 6→4, `--divider-mb` 6→4,
  `--text-pt` 10→8 — rather than from `--media-h` (raised on request) or
  `--hed-lines` (which sets this size's 100-char TITLE cap).

**Copy caps are unchanged by all of this** — TITLE 100 / SUBTITLE 220 /
HASHTAG 40, re-derived after the change. The caps are width-driven and every
headline stays on the same clamp.

`preview_insights_native.py` measures this gap on all three sizes now — a
horizontal gap where the CTA is absolute, a vertical one where it is in flow —
and flags anything under 8px:

```
  728x90   -> ...  CTA OVERLAPS TEXT (gap -27px)
  300x250  -> ...  cta gap only 6px
```

That check is what found the 300x250 case: it was never reported, only noticed
once the same measurement was applied to every size.

### Copy limits (the ad spec)

Every size clamps its headline (2 lines, 3 on the rectangle) and the 970's dek
(3 lines), so **over-long copy fails as an ellipsis, not as a broken box** —
nothing errors, the sentence just stops. These are the numbers to give an AE:

| Field | Target | Hard cap | Binding size | Notes |
|---|---|---|---|---|
| `TITLE` (headline) | **55–85 chars** | **100** | 970x250 & 300x250 | Renders at all three sizes |
| `SUBTITLE` (blurb) | **150–210 chars** | **250** | 970x250 | **970x250 only** — see below |
| `HASHTAG` | one word | **40** | 970x250 | Rendered bare — the markup no longer prefixes a `#`. Re-measured 2026-09-08 against `.insights-hero__cat`, where the category actually lives now (the old 20 probed the retired `.insights-hero__tags` node). |

Two things that surprise people:

- **The 970x250 and the 300x250 bind equally at ~100 chars**, for opposite
  reasons: the billboard gives the headline 2 lines in a ~580px column at 24px,
  the rectangle 3 lines at 15.5px in 272px. The 728x90 is still the *loosest*
  (110), which is the reverse of the intuition that the smallest box is the
  tightest — though it tightened from 125 when its CTA gutter was widened to
  stop the button overlapping the headline (see below).
- **The blurb only ever renders on the 970x250.** 728x90 and 300x250 stop at
  the headline. So the headline has to stand alone — write the dek as an
  addition, never as the second half of a sentence the headline started.

Live copy for reference: Infiniti is an **84-char** `TITLE`, Cognizant 80 —
both comfortably inside the 100 cap.

**These numbers moved on 2026-09-08** (from 85 / 200) when the CTA was added:
the CTA needed vertical room on the billboard, which was bought by narrowing the
hero column 392→340px — and the wider text measure raised the headline and dek
caps more than the CTA cost. Widening the *measure* beat squeezing the vertical
rhythm; the 728x90's caps went the other way (150→125 TITLE, 65→50 HASHTAG)
because its CTA sits in the meta row and reserves horizontal space.

**The 728x90 tightened again to 110 on the same day**, when `--cta-gutter` went
108px → 150px. Its CTA is `position: absolute`, so it is **out of flow and does
not push the headline** — the gutter is hand-reserved, and 108px was less than
the ~120px button is wide, so the headline ran *under* it (Roger: "the read more
button for the 728x90 is being overlapped"). Under-reserving an out-of-flow
gutter does not clip or wrap; it silently overlaps, which is why
`preview_insights_native.py` now measures the headline-to-CTA gap directly and
prints `CTA OVERLAPS TEXT` below 8px. The binding caps are unchanged — TITLE is
still governed by the 970x250/300x250 pair at 100.

#### These are NOT the homepage native's limits

The homepage / in-article unit (fluid style `989975`) has **no copy cap at
all**. Its stylesheet carries no `-webkit-line-clamp` and no fixed height — the
`clamp()` calls in it are font-*size* clamps (responsive type), which is easy to
misread as line clamping. Copy never truncates there; the unit just grows
taller. Measured with the Infiniti copy vs. the same copy doubled:

| Container | Normal copy | Doubled copy | Clipped? |
|---|---|---|---|
| 970px | 477px tall | 707px tall | never |
| 728px | 424px | 651px | never |
| 300px | 476px | 657px | never |

So the two surfaces fail in opposite directions: **the homepage unit absorbs
long copy by growing; the banners absorb it by ellipsing.** The practical
consequence, since one native creative's `TITLE`/`SUBTITLE` feeds *every* style
on the template:

- **Write to the banner spec (85 / 200), not the homepage's.** It is the
  strictest surface, and copy that fits it also reads fine on the homepage.
- Copy authored for the homepage native will silently truncate on the banners.
  A homepage headline is under no pressure to be short, so this is the likely
  direction of the mistake.
- The homepage unit shows the dek at every width; the banners show it on
  970x250 only.

The limits are a property of the type scale, not a constant: change a
font-size, column width or line count in the stylesheet and they move.
**Re-derive rather than trusting this table:**

```bash
python3 scripts/measure_insights_copy_limits.py
```

It binary-searches each field against every size with many randomly-built
editorial-style strings per length, and reports `safe` (every sample fits — the
number to publish) vs `max` (lucky short words). Then check a specific
creative before its flight goes live:

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

**The live styles are NOT the ones this repo created.** The originals on
template `12412102` were archived; what serves today is
**`Native (970x250|728x90|300x250)` = ids `1014148` / `1014151` / `1014379`** on
creative template **`12552841`** ("native", same seven variables), gated behind
`?nwdemocr=native`. They run byte-identical copies of
`insights_native_style.{html,css}`, so a change here reaches them only when it is
pushed onto those ids:

```bash
python3 scripts/setup_insights_native_styles.py --style-ids 1014148,1014151,1014379
python3 scripts/setup_insights_native_styles.py --style-ids 1014148,1014151,1014379 --apply
```

or dispatch `.github/workflows/push_insights_native_style.yml`. `--style-ids`
updates styles by id whatever template or name they carry, and skips the
lookup-by-name/create path entirely.

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

## On-site demo (`?nwdemocr=`)

The network already demos the *fluid* Insights unit this way: **LI 7330346837**
sits on the Newsweek_Test-2 order, targets `homepage3`, and is gated to
`nwdemocr=insighttest`. With the param the unit renders; without it, real
traffic sees nothing different.

`scripts/setup_insights_native_demo.py` does the same for the three fixed-size
banners, on **its own** nwdemocr value so the existing `insighttest` demo is
untouched:

```bash
python3 scripts/setup_insights_native_demo.py                 # dry run
python3 scripts/setup_insights_native_demo.py --apply
python3 scripts/setup_insights_native_demo.py --apply --undo  # deactivate
```

It creates the `nwdemocr=insightsbanner` value, the three native styles gated to
it, a demo line item on the test order carrying all three sizes with the same
gate, and a LICA to creative `138562612084` (a 1x1 Insights native whose
advertiser matches the test order — a LICA requires that match). Then open:

```
https://www.newsweek.com/?nwdemocr=insightsbanner
https://www.newsweek.com/insights/<any-article>?nwdemocr=insightsbanner
```

### Three GAM facts this cost a round to learn

- **A native creative associates only to a 1x1 `NATIVE` placeholder**, bound to
  the creative template. Banner-sized placeholders are rejected outright with
  `RequiredSizeError.NOT_ALLOWED @ size; trigger:'1x1-NATIVE'`. The banner sizes
  belong to the **native styles**, not to the line item — the LI stays
  native-shaped, and the style decides how each slot size renders. (The
  incumbent demo LI `7330346837` is exactly `1x1 / NATIVE / 12412102`.)
- **`skipInventoryCheck` and `allowOverbook` must be re-asserted on update**, not
  just at create. A 1x1 native placeholder forecasts ~no inventory, so an update
  without them fails with `ForecastingError.NOT_ENOUGH_INVENTORY`.
- **`createNativeStyles` returns styles as `INACTIVE`**, and a new line item is
  `INACTIVE` too. Both need activating explicitly — miss it and you get a demo
  that is fully built and silently serves nothing.

**The customTargeting question:** GAM's `NativeStyle` may not honour it at
serve time — inventory targeting certainly is honoured, custom is unverified.
The script reads each style back after creating it and prints whether the gate
stuck. **Confirmed stored** on all three demo styles (the API accepts and
persists it); whether the ad server evaluates it is still only provable by
loading a page with and without the param. If it did not, the styles are live for template `12412102` generally;
today the only *delivering* creative on that template is the already-gated demo
LI above, so the practical effect is that the `insighttest` demo would start
rendering the fixed 970x250 banner on `homepage3` instead of the fluid unit.
`--undo` deactivates the styles and pauses the demo LI.

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
