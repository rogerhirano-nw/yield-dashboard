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
- **What gets captured** — one row per size: the ad **in context** on desktop,
  plus a mobile shot for any size ≤ 400px wide (a 970x250 never needs a phone
  shot). A run-of-site line gets an extra homepage/section row.
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
| `adexclusion` | empty | `generic_brand_safety` |

`ABS` / `CBS` / `BSC` and Proximic `vnd_prx_segments` are opaque segment-id
lists, not a pass/fail — `brandsafe` is the flag. (Verified live 21 Sep 2026
against four /health articles; an earlier draft of this runbook named the wrong
keys.)

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

**In-context shots only — no close-ups.** The client wants the ad on the page,
not a crop of the creative (they already have the creative). Roger, 23 Sep 2026:
"we don't need the close up." `capture_screenshots.py` no longer takes one, the
generated shot list no longer lists one, and a deck carries one slide per
in-context shot. Don't add them back.

Dispatch `capture_screenshots.yml` with the line item and a page that passes
both tests: SOAP `getPreviewUrl` for each trafficked creative, then headless
Chromium on the live article, scrolling the slot into view before the shot.
Screenshots come back as workflow artifacts. (`preview_mobkoi_dom.yml` is the
Mobkoi forensics tool, not this.)

**`adexclusion=nopassfq` is the runner, not the page.** The site's ad code sets
`NoPassFQ` when DoubleVerify's pre-bid check (`getDvtagTargeting().IDS[0] ===
"1"`) flags the *visitor* as invalid traffic — and a headless browser on a
GitHub datacenter IP is exactly that. It says nothing about the article, so on
a page that already reads `cat` = the right vertical and `brandsafe=y`, re-run
with `no_gate` set. A `generic_brand_safety` exclusion is the real failure and
is never overridden. (Seen on every capture 23 Sep 2026; the same article read
empty on 21 Sep.)

A line with no creatives has nothing to preview — that is why step 1 reports
the creative count before you get as far as capture.

## Deck conventions

Built to the Newsweek "Paper" look the dashboard already uses, since there is no
design system on the account: warm paper `#FEFCF6`, ink `#1F1E19`, brand red
`#E91D0C` as chrome only (the eyebrow rule), Libre Baskerville over Public Sans.

Screenshots sit `object-fit:contain` on a light panel — never `cover`, which
would crop the proof, and the proof is the whole point.

Slide order: cover → campaign summary → one slide per in-context shot. Each
client gets its own deck, even when two flights share a summit — never put two
advertisers' shots in one file. Anything internal (gate overrides, blockers)
stays out of the client deck.

## Instances

| Campaign | Order | Doc | Deck | State |
| --- | --- | --- | --- | --- |
| KFSHRC — Interview, AI Health Summit 2026 | 4198147401 | [Working doc](https://claude.ai/code/artifact/c149214f-77dc-4062-8a82-0a87d1e08680) | [Deck](https://claude.ai/artifact/MU5BmCPSu3yJb7p2q6z769) | Not started; no creatives |
| American Hospital Dubai — Interview, AI Health Summit 2026 | 4194246183 | [Working doc](https://claude.ai/code/artifact/ba439cea-bbad-4124-bb35-9f5628ff1f95) | [Deck](https://claude.ai/artifact/Ays1VVMKbZFppdXo2UVbPb) | Delivering; 1 of 7 shot |
| Elevance Health — AI Health Summit 2026 | 4202666637 | — | [Deck](https://claude.ai/artifact/1dfmx5VQdvvDjXLQWfu8y9) | Shot 23 Sep 2026; 3 in-context shots |
| Becton Dickinson — AI Health Summit 2026 | 4202665578 | — | [Deck](https://claude.ai/artifact/4qGUx746WNgZv3WxfzL2dn) | Shot 23 Sep 2026; 3 in-context shots |

All four shoot against [Which Diets Could Lower Alzheimer's Risk?](https://www.newsweek.com/could-diet-reduce-alzheimers-risk-what-experts-say-12449451) — verified 21 Sep 2026 as `cat=nwus-health`, `brandsafe=y`, no ad exclusion (Elevance + BD captured 23 Sep with the `nopassfq` override above).
