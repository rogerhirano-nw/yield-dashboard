# OpenX Prebid signal audit — Newsweek_Display_Prebid (2026-08-25)

OpenX sent a bid-request field-coverage scorecard for the
`Newsweek_Display_Prebid` seat: 43 OpenRTB fields marked *Required* or
*Strongly Recommended*, each with the % of OpenX-received bid requests that
carry it. Source file: `Bid_request_fields_Display_Prebid.xlsx`.

**Answer to "can we increase coverage?": yes, for 4 fields.** The rest split
into "already correct and cannot go higher" (5) and "the wrapper already sends
it, so the loss is downstream of us" (5). 25 of the 43 fields are already at
100%.

This doc is the evidence trail. Everything below about Newsweek's own setup was
read out of **live production pages** (`www.newsweek.com`, 2026-08-25) — the
served HTML plus the Next.js JS chunks that build the ad stack — not inferred.
**Both the homepage and article pages were audited** (see *Article-page
verification* below); articles carry the impression volume, and they are also
where the per-slot bidder configuration is exposed in full.

## What Newsweek actually runs (as of 2026-08-25)

| Thing | Value |
|---|---|
| Prebid.js | **10.29.0**, self-hosted at `/prebid.js?v=10.29.0` |
| CMP | **Ketch** (`ketchcdn.com`), TCF `__tcfapi` stub present |
| GAM network | `/22541732127/newsweek/<slot>` |
| Client-side ad units | built from `adSlotConfigNodes` (per-device sizes) → `{code, mediaTypes, bids, ortb2Imp}` |
| Server-side | **90/10 sticky A/B**: `rdm` = Magnite Demand Manager PBS (`mgnipbs`, acct 9619) / `ay` = Assertive Yield PBS (`aypbs`, stored requests) |
| Other demand paths | **Amazon APS** (pubID 3376, tag-or-Prebid-adapter toggle), **TTD OpenAds** (`oajs`, s2s to `openads.adsrvr.org`) |
| Identity | sharedId/pubcid, criteo, unifiedId (TDID), identityLink, pairId, merkleId, amxId, teadsId, lotamePanoramaId, liveIntentId (US-only, 95% sampled), uid2 (conditional), pubProvidedId (`nwuid`) |
| Measurement | DV tag (`dvtag`), IAS PET |
| Relevant config | `gptPreAuction: {enabled, useDefaultPreAuction}`, `enableTIDs` (on unless CCPA opt-out), `paapi.enabled`, floors module keyed on `["mediaType","gptSlot"]` |

## Article-page verification

The homepage was the first read, but articles are where the impressions are —
and where the per-slot bidder params are served in full (the homepage ships
them stripped as `bids:[{}]`). Two articles were parsed slot by slot. Both
agree, so this is the template, not one page's quirk.

**14 display-ish slots per article**: `top`, `sticky`, `inarticle1` … 
`inarticle10`, `interstitial`, plus `video` and three out-of-page units.
Across the 42 slot×device combinations that carry a Prebid config:

| Check | Result |
|---|---|
| `mediaTypes.banner.pos` set | **0 of 42** |
| inline `ortb2Imp.ext.gpid` | 3 of 42 — the **video** slot only |
| `openx` present as a client-side bidder | **39 of 42** |

Three things follow, and one of them corrects the homepage-only reading.

**1. `banner.pos` is confirmed missing where it matters most, and the omission
is demonstrably an oversight.** Articles run **ten sequential in-article
slots**. `inarticle1` sits near the top of the story; `inarticle10` is ten
placements down, deep below the fold. **To a buyer these are indistinguishable
today** — same sizes, same everything, no position signal. That is the single
biggest yield argument in this audit, and it is invisible from the homepage,
which has only three stacked units. The tell that it's an oversight rather than
policy: the video slot passes **`pos: 1` to Amazon** (`headBidders.amazon.pos`)
while the Prebid config for the same slot has no `pos` at all. The value is
already known and already being sent — just not to Prebid.

**2. OpenX is a first-class client-side bidder, not a downstream reseller.**
It appears on 39 of 42 combos with per-slot `unit` IDs against
`delDomain: "ibt-d.openx.net"` (only `interstitial` omits it). **This corrects
the homepage-only framing** in the Tier 3 section below, which leaned on "a
Prebid Server rebuilds the imp" as the likely reason our fields don't arrive.
That explanation is much weaker now: a large share of what OpenX measures is
**our own client-side request**, so a missing field is more likely genuinely
missing from what we send than dropped by an intermediary. The live
`?pbjs_debug=true` check moves from nice-to-have to the thing that decides
Tier 3.

**3. The path-split ask survives — and the second path is now confirmed, not
inferred.** `mgnipbs` sits in the *same* `bids` array as `openx`, so a single
auction can reach OpenX twice: directly client-side, and again server-side
through Magnite's PBS. The private `ssp-prebid-params` repo (Newsweek's S2S
parameter inventory, captured from the Magnite Demand Manager Server Patterns
exports) closes it: **OpenX is in the server-side bidder roster too — display
and video, desktop and mobile — against the same `delDomain`.** Both paths
therefore land in the same OpenX seat, proven from two independent sources.

That reframes the blended percentages as a **mix ratio**. Three fields cluster
tightly — `user_id` 31.3%, `imp_ext_gpid` 33.8%, `source_tid` 37.7% — which is
what you'd expect if the client-side path (which carries all three) is roughly
a third of what OpenX receives, and the server-side path drops or regenerates
them. `imp_ext_tid` at exactly 0.0% is the outlier the live check should
explain. Treat this as the leading hypothesis, not a settled result: it is
consistent with the numbers, but only OpenX's own path split can confirm it.

**Consequence for the Assertive Yield migration.** If that reading is right,
most of the lost coverage is Prebid Server configuration, not wrapper code —
and Newsweek is mid-migration from Magnite Demand Manager to Assertive Yield
PBS. The cutover checklist in `ssp-prebid-params` covers bidder codes, account
and per-slot IDs, ads.txt/sellers.json, floors and identity modules, but has
**no signal-forwarding requirements at all** (no `gpid`, `ortb2`, `ext.tid`,
`schain`, or GPP). Porting the config as-is carries this gap forward. The
cutover is the cheapest moment to fix it — add signal forwarding to that
checklist before it happens.

On gpid specifically: the display builder **ignores** the inline `ortb2Imp` and
constructs its own from a `slotPath` that articles derive exactly like the
homepage (`` `/22541732127/newsweek/${slotName}` ``, confirmed in the article
chunk). So display units should carry gpid at runtime regardless of the config
being bare — which is why the live check, not more static reading, is the next
step.

## Tier 1 — Real gaps we can close

### 1. `imp_banner_pos` — 0% → can be ~100%

The ad-unit builder emits `mediaTypes: {[mediaType]: {sizes}}` and nothing
else. `banner.pos` is never set, so OpenRTB position is absent on every
request.

**We already compute the position string.** The GPT slot definition does:

```js
googletag.defineSlot(slotPath, sizes, adUnitCode)
  ?.addService(googletag.pubads())
  ?.setTargeting("pos", adUnitCode.replace("dfp-ad-", ""))   // "top", "sticky", …
```

So the taxonomy exists — it just never reaches the bid request. Add a
slug→`pos` map to the ad-unit builder:

```js
const POS = { top: 1, homepage1: 1, /* ATF */
              homepage2: 3, homepage3: 3, /* BTF */
              sticky: 7 /* header/footer */ };
// in the adUnit builder:
mediaTypes: { [mediaType]: { sizes, ...(POS[slug] != null && { pos: POS[slug] }) } }
```

OpenRTB values: `1` above the fold, `3` below the fold, `7` header, `4` footer,
`5` sidebar, `0`/omitted unknown. **Only label what's true** — a slot that is
ATF on desktop and BTF on mobile must resolve per device (the config is already
per-device, so this is free).

This is the highest value-per-line item on the list: viewability-sensitive
buyers bid ATF up, and today every Newsweek slot looks identical to them.

### 2. `regs_gpp` + `regs_gpp_sid` — 0% → can be ~100% of US traffic

There is **no GPP anywhere** in the stack — no `consentManagementGpp` module,
no `gppSid`, no `__gpp` call. Newsweek passes only the legacy `us_privacy`
string (49.2%, see Tier 2) and TCF for EEA.

That's a genuine compliance gap, not just a scorecard one: `us_privacy` only
encodes California. Virginia, Colorado, Connecticut, Utah, Texas, Oregon,
Montana and Florida are expressible **only** through GPP's `usnat`/state
sections, and buyers increasingly drop or discount US state traffic that
arrives without a GPP string.

**The Prebid half is already built — it just isn't switched on.** The
self-hosted bundle's own manifest (see *What's in the Prebid.js build* below)
lists **`consentManagementGpp` and `gppControl_usstates`**: both modules ship
on every page today. The wrapper never configures them — `buildConsentConfig()`
returns only `gdpr` and `usp` keys in all three geo branches, and the string
`gpp` appears **nowhere** in the wrapper chunks. So we are already paying to
download GPP support and getting nothing for it.

Fix is two-sided, and neither side needs a Prebid rebuild:
1. Enable the **GPP API** in the Ketch property config (surfaces `__gpp`).
2. Add a `gpp` block alongside the existing gdpr/usp blocks in
   `buildConsentConfig()`:
   ```js
   gpp: { cmpApi: 'iab', timeout: 3000 }
   ```
Enforcement is covered too: `gppControl_usnat` is *not* in the build, but
`gppControl_usstates` — the per-state variant, and the more granular of the
two — is.

Keep sending `us_privacy` in parallel during the transition — the IAB sunset
lets both coexist, and dropping it early would cost the 49.2% we have.

### 3. `site_publisher_name` — 54.6% → can be 100%

The wrapper builds a decent global `ortb2.site`:

```js
site: { cattax: 1, battr: [6,7], cat: dataLayer.content_channel_iabs,
        keywords: <topics>, domain: "newsweek.com", name: "Newsweek",
        ext: { data: { trsource: getTrafficSource() } } }
```

It sets `site.name` — but the audited field is **`site.publisher.name`**, which
is a different node and is never set. (The 54.6% we do get is being injected by
the server-side paths, not by us.) One-line fix in the ortb2 builder:

```js
site: { …, publisher: { name: "Newsweek", domain: "newsweek.com" } }
```

Zero risk, and it makes the publisher identity consistent across every path.

### 4. `imp_ext_tid` — 0%, worth one live check before coding

`enableTIDs` is computed as `true` unless the user is a CCPA opt-out
(`uspString === "1YYN"`), so Prebid *should* be stamping both `source.tid` and
`imp.ext.tid`. `source.tid` shows 37.7% and `imp.ext.tid` shows **exactly
0.0%** — that asymmetry can't be explained by the opt-out flag, which would hit
both equally.

The wrapper's display ad-unit builder sets `ext.gpid` and
`ext.data.pbadslot` but **never `ext.tid`** — while the TTD OpenAds builder
directly beside it explicitly does (`{tid: oajs.generateTID()}`). In Prebid 10
the tid is transmitted under the `transmitTid` activity control, gated on
`enableTIDs`; whether Prebid auto-generates `ortb2Imp.ext.tid` or expects the
publisher to supply it is the one thing minified code can't settle.

So before changing anything, open a Newsweek article with `?pbjs_debug=true`,
inspect the outgoing OpenX imp, and check whether `imp[0].ext.tid` is on the
wire. If it is present client-side, this is an OpenX-side parsing/path issue
and belongs in the reply to them, not in our backlog. If it's genuinely
missing, set it explicitly the way the TTD OpenAds builder already does:

```js
ortb2Imp: { ext: { tid: <transactionId>, gpid: slotPath, data: { pbadslot: slotPath } } }
```

## What's in the Prebid.js build

The wrapper is self-hosted, so the bundle carries its own manifest. Read from
`/prebid.js?v=10.29.0` (**built 2026-03-12** — worth a refresh cadence):

> `fpdModule, ttdBidAdapter, rubiconBidAdapter, apsBidAdapter, appnexusBidAdapter,
> pubmaticBidAdapter, openxBidAdapter, ixBidAdapter, tripleliftBidAdapter,
> kargoBidAdapter, criteoBidAdapter, teadsBidAdapter, ozoneBidAdapter,
> fwsspBidAdapter, smilewantedBidAdapter, invibesBidAdapter, atsAnalyticsAdapter,
> prebidServerBidAdapter, consentManagementTcf, tcfControl, consentManagementGpp,
> gppControl_usstates, consentManagementUsp, gamAdServerVideo, priceFloors,
> currency, gptPreAuction, schain, paapi, paapiForGpt, topicsFpdModule, rtdModule,
> timeoutRtdProvider, userId, unifiedIdSystem, uid2IdSystem, sharedIdSystem,
> pubProvidedIdSystem, identityLinkIdSystem, pairIdSystem, lotamePanoramaIdSystem,
> criteoIdSystem, teadsIdSystem, connectIdSystem, amxIdSystem, fabrickIdSystem,
> merkleIdSystem, liveIntentIdSystem`

Two things follow.

**Nothing in this audit needs a Prebid rebuild.** All four Tier 1 fixes are
ad-unit or `setConfig` changes against modules already in the bundle. The
GPP pair is the sharp case — shipped, never configured.

**`openxBidAdapter` is compiled in**, which corroborates the article-page
finding that OpenX is a genuine client-side bidder, from a second and
independent angle.

Modules present that this audit does *not* need anyone to touch: `schain`,
`priceFloors`, `currency`, `gptPreAuction`, `paapi` + `paapiForGpt`,
`topicsFpdModule`, `rtdModule` + `timeoutRtdProvider`, `fpdModule`, and
`userId` with 14 ID systems.

## Tier 2 — Already correct; do not chase these

These are scored as gaps but the low number *is* the right answer. Newsweek
should ask OpenX to re-baseline them rather than spend engineering time.

| Field | % | Why it's correct |
|---|---|---|
| `device_ip` / `device_ipv6` | 68.4 / 31.6 | **Sums to exactly 1.000000** — mutually exclusive by definition; a request carries one or the other. ~32% of Newsweek traffic is IPv6. Nothing to fix; OpenX is double-counting one field as two. |
| `user_ext_consent` | 1.2 | `buildConsentConfig()` only uses the real TCF CMP (`cmpApi:"iab"`, 8s timeout) when `gdpr_applies`; everyone else gets a static `gdprApplies:false` stub with no string. 1.2% ≈ Newsweek's EEA/UK share. Marked *Required* by OpenX, but a TCF string on US traffic would be meaningless. **One check worth doing:** if GA shows EEA/UK materially above ~1.2%, the Ketch load is timing out and that *is* a bug. |
| `regs_ext_us_privacy` | 49.2 | ≈ US traffic share; the string is only populated where CCPA/state law applies. Superseded by GPP anyway (Tier 1 #2). |
| `imp_rwdd` | 0 | Rewarded-video flag. Newsweek display web has no rewarded inventory — this should be 0. Ask OpenX to drop it from a *Display* scorecard. |
| `source_ext_omidpn` / `omidpv` | 0 | Open Measurement SDK partner/version. OMID is an in-app signal; there is no OM SDK session on web display. We *could* stamp `ortb2.source.ext.omidpn`, but declaring OM support we don't have would misrepresent measurement to buyers. Leave at 0. |

## Tier 3 — We send it; the loss is downstream

For these the wrapper code demonstrably does the right thing on the
client-side path. **Read the article-page verification above before acting on
this section:** because OpenX is a direct client-side bidder on 39 of 42 slot
combos, "an intermediary rebuilt the imp" is a weaker explanation than it looked
from the homepage alone, and these may well be genuinely missing from what we
send. The live `?pbjs_debug=true` check settles it either way.

- **`imp_ext_gpid` — 33.8%.** The builder sets it unconditionally whenever a
  `slotPath` exists, and `slotPath` is always derived as
  `` `/22541732127/newsweek/${slotName}` ``:
  ```js
  let l = {};
  if (slotPath) { l.gpid = slotPath; l.data = { pbadslot: slotPath } }
  ```
  So client-side coverage should be ~100%. This one has a **revenue** tail
  beyond the scorecard: the price-floors module is keyed on
  `schema.fields: ["mediaType","gptSlot"]`, so wherever slot resolution fails,
  floor rules fall back to default too.
- **`source_tid` — 37.7%.** See Tier 1 #4.
- **`user_buyeruid` — 63.2%.** Syncs are on and permissive
  (`filterSettings.all: {bidders:'*', filter:'include'}`, `syncDelay: 3000`,
  `auctionDelay: 1000`) but capped at **`syncsPerBidder: 3`**, and fully
  disabled for CCPA opt-outs (`syncEnabled: false`). Raising `syncsPerBidder`
  is the one lever we hold; the rest is Safari/ITP and opt-out, which cap this
  permanently. 63% is a decent match rate.
- **`user_id` — 31.3%.** Tracks publisher-provided-ID (`nwuid`) coverage, which
  is conditional on `ensureNwuid()`. Broadening first-party ID issuance to all
  users would lift it. Note the audit doesn't score `user.ext.eids` at all —
  which is where Newsweek's real identity investment lives (11 ID modules), and
  is worth more to buyers than `user.id`.
- **`site_ref` — 51.5%.** Prebid derives this from `refererInfo`
  (`document.referrer`). No `Referrer-Policy` header and no
  `<meta name="referrer">` on the page, so the browser default
  (`strict-origin-when-cross-origin`) applies and referrers are passing
  normally. The missing half is direct / bookmark / dark-social / in-app
  webview traffic. Largely organic; low ceiling.

## Beyond the scorecard: what else should be in the bidstream

OpenX scored 43 fields of its choosing. That is not the same question as *what
should a news publisher be sending*. Auditing the wrapper's `ortb2` against
what Newsweek already knows about each page turns up more, and some of it is
worth more than anything on OpenX's list.

The **complete** Prebid `ortb2.site` today is:

```js
site: { cattax: 1, battr: [6,7],
        cat: dataLayer.content_channel_iabs || [],
        keywords: <topics, comma-joined>,
        domain: "newsweek.com", name: "Newsweek",
        ext: { data: { trsource: getTrafficSource() } } }
```

That is the whole object. There is **no `site.content`, no `site.publisher`,
and no `user` object anywhere** (`ortb2.user` appears in zero wrapper chunks).

### 1. `site.content` is entirely absent — the biggest gap for a news publisher

Every input is already on the page, in the markup or the Next.js payload:

| ORTB field | Already on the page as |
|---|---|
| `content.title` | the headline |
| `content.cat` + `cattax` | `iab_context_v1` / `iab_context_v2` |
| `content.keywords` | `orderedKeywords` — structured objects with topic URIs |
| `content.language` | `<html lang="en">` |
| `content.url` | the canonical URL |
| `content.context: 5` | it's a text article |
| recency | `article:published_time` / `article:modified_time` meta tags |
| section | `article:section` (e.g. "Personal Finance") |
| reading time | `readingTime` |

Article-level signals currently sit at **site** level or nowhere: `site.cat`
receives the *article's* IAB categories, which is the wrong node for them.
Contextual and news-sensitive buyers that filter on `site.content.cat`,
`content.language`, or content recency see nothing at all today.

### 2. Zero first-party audience signal (`user.data`)

`ortb2.user` is never set. The page knows the reader's **logged-in state**
(`<html className="guest">`), **paywall status** (`setIsPaywalledPage`), and
section/topic affinity. Passing those as `user.data` segments with a declared
`segtax` is the standard mechanism, and post-cookie it is the signal buyers
actually pay a premium for. **This is the highest-value yield item in this
document** — higher than anything OpenX flagged.

### 3. The IAB taxonomy we send is the deprecated one

The page computes **three** versions per article and we send the oldest:

- `iab_context_v1` → `["IAB13-2"]`, `["IAB12-2"]` — legacy 1.0 string IDs
- `iab_context_v2` → `["406"]`, `["672"]` — 2.x numeric IDs, **computed and unused**
- `iab_context_v3` → `[]` — plumbed, never populated

`content_channel_iabs` (what reaches `site.cat`) is the **v1** value, and
`cattax: 1` correctly declares it as Taxonomy 1.0 — so this is *consistent*,
not a bug. But 1.0 is deprecated and most DSPs now prefer 2.2/3.0. The v2 IDs
are sitting right there. ORTB 2.6 lets you send both at once: keep
`site.cat`/`cattax`, and add the newer taxonomy as a `content.data` entry with
its matching `segtax`. **Verify which 2.x version `iab_context_v2` actually is
before declaring a `cattax`** (2.0 → 2, 2.1 → 5, 2.2 → 6) — declaring the wrong
one is worse than sending v1.

### 4. Amazon is fed signals Prebid isn't — three times over

The APS init builds its own site object with `mobile: 1`, `amp: 0`,
`privpolicy: 1`, `ext.sitetaxonomy` (= `dataLayer.section`) and `kwarray`
(topics as an **array**). Prebid's `ortb2` gets **none of those**, and receives
keywords as a comma-joined string rather than the array form ORTB 2.6 prefers.

Together with the `pos: 1` the video slot passes to Amazon and not Prebid, that
is **three separate places where the Amazon path is enriched and the Prebid
path isn't**. Worth treating as one systemic habit rather than three
coincidences: whoever adds a signal is adding it to APS and stopping there.
`site.privacypolicy` and `site.mobile` are standard ORTB fields and free.

### 5. No `badv` / `bcat`

Neither appears anywhere. Newsweek runs a Confiant → GAM Protection blocklist
pipeline that pushes flagged advertiser domains daily
(`docs/confiant_blocklist.md`) — none of it reaches the bidstream, so bad
demand is caught *after* it bids rather than suppressed before. Worth a
judgement call rather than a straight yes: `badv` shapes demand, and an
over-broad list costs fill. `battr: [6,7]` is already set.

### Checked and fine — don't chase

- **`device.sua`** (structured user agent) — Prebid auto-populates it, and
  `userAgentData` / `getHighEntropyValues` are both in the bundle. As UA
  strings get reduced this is the replacement, and we already have it.
- `schain`, price floors, `imp.video.plcmt`, `battr` — all set.

## The one thing to ask OpenX for

**A breakdown of the same table by schain / seller / integration path.**

A single blended percentage per field is a *mixture*. Newsweek reaches OpenX
through at least four routes — client-side Prebid.js, Magnite Demand Manager
PBS (90% of sessions), Assertive Yield PBS (10%), and TTD OpenAds — and a
Prebid Server reconstructs the imp rather than forwarding ours. A field at 33%
could be "missing on 2/3 of our pages" (our bug) or "dropped by one
intermediary" (their bug or the SSP's), and those look identical in this
report. Every Tier 3 item is blocked on that split.

Worth stating plainly in the reply: three of the five Tier 2 fields
(`imp_rwdd`, both OMID fields) are in-app signals on a display-web scorecard,
and `device_ip`/`device_ipv6` is one field counted as two. Correcting the
denominator would move the seat's apparent score substantially without a line
of code.

## Suggested order of work

1. `site.publisher.name` — one line, zero risk.
2. `banner.pos` — small map, real yield upside.
3. Live `pbjs_debug` check on `imp.ext.tid` + `gpid` → decides Tier 3. Now
   load-bearing: OpenX bids client-side on nearly every slot, so Tier 3 is
   plausibly ours rather than an intermediary's.
4. GPP — a `gpp` block in `buildConsentConfig()` plus Ketch's GPP API. The
   modules are already in the bundle, so this is **config, not a rebuild** —
   cheaper than first estimated, and the largest compliance exposure. The
   Ketch-side switch is the only real dependency.
5. Reply to OpenX with the Tier 2 re-baseline and the path-split request.

## Provenance

Findings were read from production on 2026-08-25: `curl` against the homepage
**and two article pages** for the served HTML and the ad bootstrap, then the
`/_next/static/chunks/*.js` bundles for the wrapper config
(`buildFullPrebidConfig`, `buildConsentConfig`, `getSharedUserIds`,
`getPbsVendor`, `applyORTB2ToPrebid` and the ad-unit builder). No credentials
or internal access were needed. Re-run the same fetch to re-verify after any
wrapper deploy — the chunk hashes change per build.
