# Publisher Provided Signals (PPS) — Newsweek setup

Publisher Provided Signals let us attach Newsweek's first-party **contextual**
(and optionally **audience**) data to programmatic ad requests as standardized
**IAB taxonomy** category IDs, so buyers (Google demand, Authorized Buyers,
Open Bidding, SDK Bidding) can target our inventory more precisely. This is a
**yield lever** — better signals → more/stronger bids — not a dashboard feature.
PPS is configured **outside this repo** (on the website's GPT tag, or in the
GAM UI); this repo only holds the snippet + this runbook.

Supported taxonomies today: **IAB Audience Taxonomy 1.1** and **IAB Content
Taxonomy 2.2**.

## Two ways a publisher can provide PPS

| | Path A — GAM-managed mapping | **Path B — pass at ad request time (this repo)** |
|---|---|---|
| Where | Ad Manager UI: **Signals → Publisher provided signals** | The website's GPT tag, via `googletag.setConfig({ pps: … })` |
| How | Map existing audience segments / key-values / CMS metadata → IAB categories. "If you're already passing key-values and audience data, you don't need to pass new data — only map it." | The page resolves the article section → IAB Content 2.2 IDs and sets them at request time |
| Eligibility | Ad Manager **360** only (Beta); needs *Edit publisher provided signals* permission | Any GPT publisher |
| Engineering | None (config-only) | A page-tag change (the snippet below) |

Use **one** route per signal type — don't both map a key-value in the UI *and*
pass the same content IDs via GPT.

We chose **Path B for contextual content signals** (we control the page tag and
already know each article's section). Path A remains the no-code option if we'd
rather map our existing GAM key-values in the UI instead.

## Path B — deploying the snippet

Snippet: [`docs/snippets/pps_content_signals.js`](snippets/pps_content_signals.js).

1. **Wire the section source.** The snippet's `resolveSections()` tries several
   common sources (a `window.NW_PAGE` object, `dataLayer`,
   `<meta property="article:section">`, a `data-section` attribute, then the URL
   path). Confirm which one Newsweek's CMS actually exposes on the page and keep
   that branch (drop the rest, or leave them as fallbacks).
2. **Review the mapping.** `SECTION_TO_IAB` maps our normalized section slugs to
   IAB Content Taxonomy 2.2 Unique IDs (tier-1 baseline, tier-2 where our
   sections are specific). The IDs are seeded from the canonical sheet but the
   *section→category* choices are editorial — have RevOps eyeball them. Add any
   section we publish that isn't listed.
3. **Place the call before the first ad request.** `setConfig` is read at request
   time, so the snippet must run inside `googletag.cmd` **before**
   `pubads().refresh()` / the initial `googletag.display()`. A late call only
   affects slots requested after it.
4. **QA.** Open the GPT **Publisher Console** (`?googfc` on the page) and confirm
   the PPS config is present, or read the `nw_pps_set` dataLayer event the
   snippet pushes. Spot-check a few sections resolve to sane categories.

### The config it emits

```js
googletag.setConfig({
  pps: {
    taxonomies: {
      'IAB_CONTENT_2_2': { values: ['379', '386'] }   // e.g. a Politics article
    }
  }
});
```

## Audience signals (optional, later)

Audience signals (`IAB_AUDIENCE_1_1`) are **off by default** in the snippet —
they require (a) a real Newsweek-segment → IAB Audience 1.1 lookup and (b)
sending only on **ads-personalization-allowed** requests (consent). When we have
both, enable the commented `IAB_AUDIENCE_1_1` block. Audiences are often easier
via **Path A** (map existing GAM audience segments in the UI) since the segment
membership already lives in GAM.

## IAB ID reference

Seed tier-1 IDs used in the mapping (IAB Content Taxonomy 2.2 Unique ID):

| Category | ID | Category | ID |
|---|---|---|---|
| News and Politics | 379 | Sports | 483 |
| Business and Finance | 52 | Healthy Living | 223 |
| Personal Finance | 391 | Medical Health | 286 |
| Technology & Computing | 596 | Automotive | 1 |
| Science | 464 | Travel | 653 |
| Pop Culture | 432 | Food & Drink | 210 |
| Movies | 324 | Style & Fashion | 552 |
| Education | 132 | Real Estate | 441 |
| Video Gaming | 680 | Religion & Spirituality | 453 |

Canonical source (verify before launch):
<https://github.com/InteractiveAdvertisingBureau/Taxonomies> →
`Content Taxonomies/Content Taxonomy 2.2.tsv`.

## References

- GPT `PublisherProvidedSignalsConfig` —
  <https://developers.google.com/publisher-tag/reference#googletag.config.PublisherProvidedSignalsConfig>
- Ad Manager: About PPS —
  <https://support.google.com/admanager/answer/12451124>
- Ad Manager: Map key-values & audience segments to taxonomies —
  <https://support.google.com/admanager/answer/15287826>
- Ad Manager: Share PPS at time of ad request —
  <https://support.google.com/admanager/answer/15287325>
