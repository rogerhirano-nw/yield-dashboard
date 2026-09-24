# Screenshots Documents (proof of placement)

How to build the screenshots deliverable for any Direct campaign. Nothing in
this path is campaign-specific — you give it a GAM order id and it gives you
the doc body.

## The two steps

**1. Pull the source.** Dispatch `pull_screenshots_source.yml` with either an
order id or a line item id — a line item resolves its own order, which is
handy because a GAM deep link carries the line item:

```bash
gh workflow run pull_screenshots_source.yml -f line_item=7432006947
```

Give `-f order=<id>` instead to cover every line on an order, or both to
highlight one line within it.

It runs `scripts/pull_screenshots_source.py`, which reads — never writes —
the order, its line items (status, type, flight, creative sizes, goal,
targeted ad units) and every associated creative (type, size, click URL, GAM
asset URL, preview URL). Two artifacts come back:

- `screenshots_source.json` — the raw facts
- `screenshots_doc.md` — the doc body, ready to paste

GAM creds are Actions-only, so this has to run in CI; the script also runs
locally against a `.env` that carries `GAM_SERVICE_ACCOUNT_JSON` +
`GAM_NETWORK_ID` if you ever have them.

**2. Build the doc** from `screenshots_doc.md` — the working page: verified
facts, blockers, and the page-selection rules.

**3. Build the deck — always a PowerPoint file.** The client deliverable is a
`.pptx`, every time (Roger, 23 Sep 2026: "the output must be always in
powerpoint"). `scripts/build_screenshots_deck.py spec.json "<Advertiser> -
Proof of Placement.pptx"` builds it (needs `python-pptx`): cover → campaign
summary → one slide per in-context shot (point each at its `_framed` copy),
each captioned with URL, capture time and size → a closing slide naming the
seller. The spec format is in the script's docstring. The deck is what ships to
the client; the doc stays internal and links to it.

## What the generated body contains

`build_markdown()` derives all of it from the pull — it is not a fixed
template:

- **Lead** — states whether screenshots are possible *now*, naming the actual
  reason if not (flight hasn't opened, no creatives attached).
- **Campaign** — advertiser, order, line item, flight, goal, sizes, targeting,
  PO/IO.
- **What gets captured** — one in-context row per size on desktop, plus a
  mobile row for any size ≤ 400px wide (a 970x250 never needs a phone shot).
  A run-of-site line gets an extra homepage/section row.
- **Before capture** — only the blockers that actually apply, as checkboxes.
- **Screenshots** — the placeholder the images land in.

## Picking the page

Two tests, both before the shot is taken. The generator emits this per campaign.

**In the client's industry.** The vertical is token 2 of the order name
(`Newsweek_Direct_`**`Health`**`_...`), mapped to a category slug in
`_VERTICAL_SLUGS`. Shooting a hospital's ad beside a celebrity divorce story
reads as careless and the client notices. Verify on the page: `cat` / `sitecat`
is `nwus-` + the primary category slug, hyphens as underscores.

**Do not trust the section listing.** Newsweek cross-posts editorially, so an
article sitting under /health may carry a different `cat` entirely — one checked
on 21 Sep 2026 read `nwus-family_parenting`. Only the page's own key-value
counts.

**Brand safe.** On-topic is not enough — a malpractice suit, an outbreak or a
death story is squarely in a hospital's vertical and squarely the wrong page to
hand them. Newsweek already classifies this, so read it off the page instead of
judging by the headline:

| Key-value | Passes | Fails |
| --- | --- | --- |
| `brandsafe` | `y` | `n` |
| `adexclusion` | no `*brand_safety*` label | `generic_brand_safety` |

`ABS` / `CBS` / `BSC` and Proximic `vnd_prx_segments` are opaque segment-id
lists, not a pass/fail — `brandsafe` is the flag. (Verified live 21 Sep 2026
against four /health articles; an earlier draft of this runbook named the wrong
keys.)

**`adexclusion` is a label list, not a verdict — only a brand-safety label
fails the page.** The runbook first read "adexclusion must be empty", which
was right on the day it was written and wrong the next: on 22 Sep 2026 the
site carried **`nopassfq`** on health articles that were `brandsafe=y`,
including the very article pinned below, and the gate refused all of them. The
two signals travel together on a genuine failure — the one article that failed
that day read `brandsafe=n` *and* `generic_brand_safety`. So the test is
`brandsafe=y` plus no label containing `brand_safety`; any other label is
printed for the record and does not block.

A failing page gets skipped, not cropped around.

For a run-of-site line the page choice is a presentation decision for the
document, not something the trafficking guarantees — the generated body says so
rather than letting the shot imply contextual targeting that was never bought.

Add a vertical to `_VERTICAL_SLUGS` when a new one turns up; an unmapped or
`NA` vertical still gets the two tests, just without the slug hint.

Two things the body always says, because both have bitten us:

- GAM end times are network-tz instants — a line ending 13 Oct 23:59 ET reads
  as 14 Oct in UTC.
- newsweek.com article content slots are **lazily defined**, so a screenshot
  taken at page load catches an empty well. The page has to be scrolled to the
  slot first. See `docs/mobkoi_viewability.md` and the site-stack notes.

It also flags, when true, that an advertiser name containing `[nw]` is excluded
from the dashboard's Direct table — that campaign won't show up there while it
runs.

## Worked example (21 Sep 2026)

Four /health listing articles, checked live for the American Hospital Dubai
flight:

| Article | `cat` | `brandsafe` | Verdict |
| --- | --- | --- | --- |
| AI medical advice / parents | `nwus-family_parenting` | `y` | Fails — wrong vertical |
| Chronic stress and the heart | `nwus-health` | `n` | Fails — flagged unsafe |
| One type of sleep, 83 diseases | `nwus-health` | `n` | Fails — flagged unsafe |
| Which diets could lower Alzheimer's risk | `nwus-health` | `y` | **Passes** |

Two of the four are in the right vertical and still unusable. That is the whole
reason for the second test.

## Capturing the images

Dispatch `capture_screenshots.yml` with the line item and the article URL:

```bash
gh workflow run capture_screenshots.yml \
  -f line_item=7432006947 \
  -f article_url=https://www.newsweek.com/...
```

It runs `scripts/capture_screenshots.py` — SOAP `getPreviewUrl` per creative,
then headless Chromium on the live article at desktop (1600px) and mobile
(390px), scrolling the lazy slot into view before the shot. It enforces both
page tests above before shooting anything (`-f no_gate=true` is the deliberate
override), skips any creative too wide for the viewport, and hides the Ketch
consent overlay rather than clicking it. Screenshots come back as workflow
artifacts, two per creative per viewport (`_context` and `_crop`).

Every context shot also comes back as a `_framed` copy in a device frame —
an iPhone on mobile, a MacBook Pro on desktop — drawn by
`scripts/frame_device_shot.py`, and that is the one that goes in the deck (see
the conventions below). Run it by hand on any shot:
`python scripts/frame_device_shot.py shot.png` (it picks the device from the
shot's orientation; `--device phone|laptop` overrides).

`preview_mobkoi_dom.yml` is NOT the tool for this — it is mobile-only DOM
forensics, so a 970x250 falls through to a Newsweek house ad and the "proof"
shows the wrong advertiser.

A line with no creatives has nothing to preview — that is why step 1 reports
the creative count before you get as far as capture.

## Deck conventions

**Format: `.pptx`, always** — built by `scripts/build_screenshots_deck.py`, one
file per advertiser, named `<Advertiser> - Proof of Placement.pptx`. A browser
deck (the claude.ai Slides artifact) is fine as a preview, but it is not the
deliverable. The `.pptx` uses Cambria over Calibri, which ship with Office, so
it renders as built on the client's machine.

Built to the Newsweek "Paper" look the dashboard already uses, since there is no
design system on the account: warm paper `#FEFCF6`, ink `#1F1E19`, brand red
`#E91D0C` as chrome only (the eyebrow rule), Libre Baskerville over Public Sans.

Screenshots sit `object-fit:contain` on a light panel — never `cover`, which
would crop the proof, and the proof is the whole point.

**In-context shots only — no close crops** (Roger, 22 Sep 2026). The proof is
the ad sitting in the page; a crop of the creative is a picture of an asset the
client already has, and it doubles the deck for nothing. The capture script
still writes a `_crop` file per shot, which is worth a look to confirm the
creative rendered legibly — it just doesn't become a slide.

**Shots go in a device frame** (Roger, 24 Sep 2026) — mobile in an iPhone,
desktop in a MacBook Pro. A bare screenshot reads as a cropped page; the frame
says which device it is before anyone reads the caption. Desktop captures are
1600x1000, which is 16:10, so they fill a MacBook lid exactly.

**The frame never covers the capture.** Bezel, corner rounding, side buttons
and the base edge sit outside the screenshot; no notch, island, status bar or
menu bar is painted over it, and the unframed original is kept beside the
framed copy. The screenshot is the evidence, and a deck that retouches it is
worth less than one that doesn't. That rule is why the laptop gets a camera dot
in its top bezel rather than the notch a real MacBook Pro has: a real notch
hangs down into the display, so drawing one would either cover captured pixels
or read as a grey tab stuck to the bezel.

Slide order: cover → campaign summary → one slide per shot → a closing slide
naming the seller. Any internal status slide comes out before the deck goes to
the client. **One deck per advertiser**, even when two flights share a summit —
never put two advertisers' shots in one file.

**The last slide always names the seller** (Roger, 24 Sep 2026) — the AE who
sold it, so whoever opens the deck knows whose campaign it is. The seller is
the last token of the order name (`..._Team-INTL_`**`AShah`**), resolved
through settings.json's `ae_names` so it reads "Amit Shah" and not "AShah";
that map carries the case variants, which is why the lookup tries the token
as-is first. `pull_screenshots_source.py` puts it in the Campaign table, so it
is in the source every deck is built from. In the `.pptx` it is its own closing slide
(`seller` in the spec), so it survives the internal status slide coming out.

## Instances

| Campaign | Order | Doc | Deck | State |
| --- | --- | --- | --- | --- |
| KFSHRC — Interview, AI Health Summit 2026 | 4198147401 | [Working doc](https://claude.ai/code/artifact/c149214f-77dc-4062-8a82-0a87d1e08680) | [Deck](https://claude.ai/artifact/MU5BmCPSu3yJb7p2q6z769) | Live 22 Sep; **shot in full** (3 slides) |
| American Hospital Dubai — Interview, AI Health Summit 2026 | 4194246183 | [Working doc](https://claude.ai/code/artifact/ba439cea-bbad-4124-bb35-9f5628ff1f95) | [Deck](https://claude.ai/artifact/Ays1VVMKbZFppdXo2UVbPb) | Delivering; 1 of 7 shot |
| Elevance Health — AI Health Summit 2026 | 4202666637 | — | [Deck](https://claude.ai/artifact/1dfmx5VQdvvDjXLQWfu8y9) · `.pptx` | Shot 23 Sep 2026; 3 framed in-context shots + seller slide |
| Becton Dickinson — AI Health Summit 2026 | 4202665578 | — | [Deck](https://claude.ai/artifact/4qGUx746WNgZv3WxfzL2dn) · `.pptx` | Shot 23 Sep 2026; 3 framed in-context shots + seller slide |

Verified health pages to shoot against — re-check on the day, since `adexclusion`
changes under a URL (both read `cat=nwus-health`, `brandsafe=y`):

- [Scientists Find Potential Way To Preserve Muscle During GLP-1 Weight Loss](https://www.newsweek.com/scientists-find-potential-way-to-preserve-muscle-during-glp-1-weight-loss-12468110) — the KFSHRC shots, 22 Sep 2026
- [Which Diets Could Lower Alzheimer's Risk?](https://www.newsweek.com/could-diet-reduce-alzheimers-risk-what-experts-say-12449451) — the American Hospital Dubai shot, 21 Sep 2026; Elevance Health and Becton Dickinson, 23 Sep 2026

**Count the slides from what the capture can actually produce.** KFSHRC has two
sizes and came to three slides: 970x250 desktop, 300x250 desktop, 300x250
mobile. The 970x250 gets no mobile shot at all (it cannot fill a 390px slot —
the capture script skips it rather than shoot a house ad), close crops are not
part of the deliverable (above), and the homepage/section row is only worth a
slide if the client asks. Cut the slides the capture didn't produce instead of
leaving "awaiting capture" placeholders in a deck that is otherwise done.
