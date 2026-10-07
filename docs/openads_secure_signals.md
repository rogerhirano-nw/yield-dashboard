# OpenAds and Google secure signals: `_pbjsGlobals` order

## The problem

GAM is set to **"Use your Prebid configuration to automatically configure your
secure signals settings."** With that on, GPT reads user IDs from the *first*
Prebid instance in `window._pbjsGlobals` and forwards them to Open Bidding in
each ad request's `a3p` parameter.

newsweek.com runs two Prebid builds: our main wrapper (`pbjs`,
`/prebid.js`, v11.35.0) and TTD's OpenAds (`oajs`). OpenAds' Drawbridge copies
our resolved IDs into its *bid requests*, not into its own ID store, so
`oajs.getUserIdsAsEids()` returns `adserver.org` only. When `oajs` registers
first, Open Bidding gets `adserver.org` plus GAM's own ESP signals
(`esp.criteo.com`, `rtbhouse`). SharedID, LiveIntent and the rest never arrive.

**Why the order is a race.** The site is Next.js. `openads-sdk` and `prebid`
are both `<Script strategy="afterInteractive">`, which Next inserts as async
scripts in the same millisecond. Whichever file finishes downloading first
registers first. The OpenAds CDN file usually wins, so moving the `<Script>`
higher in the layout changes nothing. The fix is to inject OpenAds from
`pbjs.que` (it only runs after `pbjs` has registered) and keep its
`<link rel="preload">` so the download still starts early. TTD confirmed on
2026-10-07 that OpenAds and Drawbridge don't depend on their position in
`_pbjsGlobals`.

## The check

`CHECK=signals` in `scripts/prebid_render_forensics.py` loads article pages in
headless Chromium, each one fast and throttled (~Slow 4G), and records per load:

* the order instances registered in `_pbjsGlobals` (the push is hooked before
  any page script runs);
* the ID **source names** in the decoded `a3p` of the first GPT ad request and
  of all requests. `a3p` is URL-safe base64 of a protobuf whose repeated field
  2 holds one signal each, source domain in sub-field 1. ID values are never
  decoded or printed, because the output lands in public logs;
* `pbjs` and `oajs` `getUserIdsAsEids()` sources;
* whether the first ad request went out before `pbjs` registered.

It ends in PASS/FAIL lines: `pbjs` first on every load, and `EXPECT_SOURCES`
(default `pubcid.org`) in the first ad request's `a3p` on every load.

```
CHECK=signals LOADS=5 python scripts/prebid_render_forensics.py
# against QA (article links are scraped from HOME_URL's host):
CHECK=signals HOME_URL=https://<qa-host>/ STRICT=1 python scripts/prebid_render_forensics.py
```

Or dispatch `.github/workflows/prebid_render_forensics.yml` with `check=signals`.
Knobs: `THROTTLE` (`off,on`), `PROFILES`, `EXPECT_SOURCES`, `STRICT=1` (exit
non-zero on any FAIL), `ARTICLE_URLS`.

## Baseline (2026-10-07, production, before the fix)

8 loads (2 articles × mobile/desktop × fast/throttled):

* `oajs` registered first on **7/8**. Those loads' `a3p`:
  `adserver.org, esp.criteo.com, rtbhouse`.
* On the one load where `pbjs` won the race (mobile, fast), the first `a3p`
  carried `pubcid.org`, `liveintent.com` and its partner domains, `pubmatic.com`,
  `rubiconproject.com`, `openx.net`, `bidswitch.net` and the rest, which is
  the outcome the fix should make the rule.
* The first GPT ad request never went out before `pbjs` registered (0/8), so
  `pbjs` is in place by the time GPT reads IDs once the order is fixed.
* `pbjs` registers twice in `_pbjsGlobals` on every load: a double include in
  the Prebid build, harmless to ordering.
