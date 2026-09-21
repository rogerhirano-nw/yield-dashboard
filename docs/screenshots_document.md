# Screenshots Documents (proof of placement)

How to build the screenshots deliverable for any Direct campaign. Nothing in
this path is campaign-specific — you give it a GAM order id and it gives you
the doc body.

## The two steps

**1. Pull the source.** Dispatch `pull_screenshots_source.yml` with the order
id (and optionally a line item id to highlight when the order has several
lines):

```bash
gh workflow run pull_screenshots_source.yml -f order=4198147401 -f line_item=7432006947
```

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

**3. Build the deck** — the shots go in a slide deck, not in the doc. One slide
per shot, each with a caption line for URL, date and size, plus a cover and a
campaign-summary slide. The deck is what ships to the client; the doc stays
internal and links to it.

## What the generated body contains

`build_markdown()` derives all of it from the pull — it is not a fixed
template:

- **Lead** — states whether screenshots are possible *now*, naming the actual
  reason if not (flight hasn't opened, no creatives attached).
- **Campaign** — advertiser, order, line item, flight, goal, sizes, targeting,
  PO/IO.
- **What gets captured** — one row per size: context + close crop on desktop,
  plus the mobile pair for any size ≤ 400px wide (a 970x250 never needs a
  phone shot). A run-of-site line gets an extra homepage/section row.
- **Before capture** — only the blockers that actually apply, as checkboxes.
- **Screenshots** — the placeholder the images land in.

## Picking the page

Two tests, both before the shot is taken. The generator emits this per campaign.

**In the client's industry.** The vertical is token 2 of the order name
(`Newsweek_Direct_`**`Health`**`_...`), mapped to a category slug in
`_VERTICAL_SLUGS`. Shooting a hospital's ad beside a celebrity divorce story
reads as careless and the client notices. Verify on the page: `cat` / `sitecat`
is `nwus-` + the primary category slug, hyphens as underscores.

**Brand safe.** On-topic is not enough — a malpractice suit, an outbreak or a
death story is squarely in a hospital's vertical and squarely the wrong page to
hand them. Verify on the page: `adexclusion` empty, and the brand-safety
key-values (`ABS` / `CBS` / `BSC`, Proximic `vnd_prx_segments`) clean. A page
carrying an exclusion or a negative segment gets skipped, not cropped around.

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

## Capturing the images

Use the `preview_mobkoi_dom.yml` path: SOAP `getPreviewUrl` for the trafficked
creative, then headless Chromium on a live article page, scrolling the slot
into view before the shot. Screenshots come back as workflow artifacts.

A line with no creatives has nothing to preview — that is why step 1 reports
the creative count before you get as far as capture.

## Deck conventions

Built to the Newsweek "Paper" look the dashboard already uses, since there is no
design system on the account: warm paper `#FEFCF6`, ink `#1F1E19`, brand red
`#E91D0C` as chrome only (the eyebrow rule), Libre Baskerville over Public Sans.

Screenshots sit `object-fit:contain` on a light panel — never `cover`, which
would crop the proof, and the proof is the whole point.

Slide order: cover → campaign summary → one slide per shot → an internal status
slide that comes out before the deck goes to the client.

## Instances

| Campaign | Order | Doc | Deck |
| --- | --- | --- | --- |
| KFSHRC — Interview, AI Health Summit 2026 | 4198147401 | [Working doc](https://claude.ai/code/artifact/c149214f-77dc-4062-8a82-0a87d1e08680) | [Deck](https://claude.ai/artifact/MU5BmCPSu3yJb7p2q6z769) |
