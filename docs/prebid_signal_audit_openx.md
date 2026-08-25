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

Fix is two-sided and Ketch already supports its half:
1. Enable the **GPP API** in the Ketch property config (surfaces `__gpp`).
2. Add Prebid's **`consentManagementGpp`** module and configure it alongside
   the existing gdpr/usp blocks in `buildConsentConfig()`:
   ```js
   gpp: { cmpApi: 'iab', timeout: 3000 }
   ```
3. Add the **`gppControl_usnat`** activity-control module so Prebid actually
   enforces the sections rather than just forwarding the string.

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

Before changing anything, open a Newsweek article with `?pbjs_debug=true`,
inspect the outgoing OpenX imp, and check whether `imp[0].ext.tid` is on the
wire. If it is present client-side, this is an OpenX-side parsing/path issue
and belongs in the reply to them, not in our backlog. If it's genuinely
missing, set it explicitly the way the TTD OpenAds builder already does:

```js
ortb2Imp: { ext: { tid: <transactionId>, gpid: slotPath, data: { pbadslot: slotPath } } }
```

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
4. GPP (Ketch config + `consentManagementGpp` + `gppControl_usnat`) — largest
   effort, largest compliance exposure.
5. Reply to OpenX with the Tier 2 re-baseline and the path-split request.

## Provenance

Findings were read from production on 2026-08-25: `curl` against the homepage
**and two article pages** for the served HTML and the ad bootstrap, then the
`/_next/static/chunks/*.js` bundles for the wrapper config
(`buildFullPrebidConfig`, `buildConsentConfig`, `getSharedUserIds`,
`getPbsVendor`, `applyORTB2ToPrebid` and the ad-unit builder). No credentials
or internal access were needed. Re-run the same fetch to re-verify after any
wrapper deploy — the chunk hashes change per build.
