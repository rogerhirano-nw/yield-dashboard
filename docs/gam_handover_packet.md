# GAM knowledge handover packet

A self-contained brief on Newsweek's Google Ad Manager setup and the ad-ops
know-how built up in this repo, written so a **new Claude account** (or a new
person) can pick up the work cold. Load it as Project knowledge in claude.ai,
or give the new account access to this repo; `CLAUDE.md` loads on its own
there and this file is the GAM-only digest of it.

Compiled 2026-10-01 from `CLAUDE.md`, `docs/`, `gam_client.py` and `scripts/`.
When this packet and a linked doc disagree, the linked doc is newer.

---

## 0. What does and doesn't transfer

| Moves with the repo (nothing to do) | Has to be set up again on the new account |
|---|---|
| `CLAUDE.md` (loads on its own in Claude Code) | **GitHub access**: connect GitHub and install the Claude app on `rogerhirano-nw/yield-dashboard` |
| All `docs/*.md` runbooks and debriefs | **claude.ai connectors** (Gmail, Drive, Airtable, etc.): connect each one again |
| Every script and Actions workflow | **Supabase / beehiiv MCP**: OAuth from a local `claude` session (`/mcp`) |
| GAM credentials (stored as **repo Actions secrets**, not in any Claude account) | **Claude memory notes** (e.g. `project_yield_dashboard_ops`): these live in the old account's local Claude config. Copy them by hand or use the import-memory feature |
| | Old chat history: it doesn't migrate. The repo docs and changelog hold what matters |

**GAM credentials never touch a Claude account.** `GAM_SERVICE_ACCOUNT_JSON`
and `GAM_NETWORK_ID` are GitHub Actions secrets. Cloud sessions have no local
creds, so every GAM read or write runs as a dispatched workflow. A local Mac
run reads them from `~/code/yield-dashboard/.env`.

---

## 1. Network and access

- **Network**: `22541732127`, time zone **America/New_York**.
- **APIs**: the REST `google-ads-admanager` v1 client (`gam_client.GAMClient`)
  handles reporting, line items, orders, users and private auctions. The legacy
  **SOAP API (`googleads`, version `v202605`)** covers everything v1 lacks:
  creatives, LICAs, line-item writes/renames, ProposalLineItems (PD/PG),
  native styles, YieldGroupService and **ForecastService** (REST has no
  forecasting at all). SOAP calls get 3 retries (`_soap_retry`).
- **Service account limits**:
  - **Can** create line items, creatives, LICAs and native styles, and archive PLIs.
  - **Cannot** create ad units (`PERMISSION_DENIED`), approve orders, or
    activate LIs on an approved order. **Order approval is UI-only.** A new LI
    added to an approved order stays INACTIVE until the order is re-approved
    in the UI, then takes about 10 minutes to start winning.
- **Pulling from a cloud session**: copy `.github/workflows/pull_index_ob_requests.yml`
  (it uses the repo secrets and posts stdout as a PR comment). Without `gh`,
  dispatch through the GitHub MCP tools and fetch artifacts through the REST API.

### Key ids

| Thing | Id |
|---|---|
| `newsweek` site root ad unit (site display book) | 23207092721 |
| `oop1` out-of-page (article sponsor logo) | 23207087801 |
| `oop2` (doesn't render on article templates) | 23207098418 |
| `homepage3` (fluid Insights native) | 23207094803 |
| Interstitial unit used by AppleTV+ template | 23295929518 |
| Yield groups (100% Open Bidding, no Mediation) | `display` 680328, `video` 680331 |
| Internal test orders (hidden from dashboard) | `3648897741` (GMC TEST PAGE), `4082002976` (Newsweek_Test-2) |
| Confiant brand-safety Protection ("Everything") | 28044902 |
| United States Geo_Target | 2840 |
| `[nw] Omnicom` advertiser (Apple TV+/OMD) | 5744377675 |
| Salesperson "Newsweek - Sales - Ivy Lee" | 255224230 |

---

## 2. Naming conventions (everything downstream parses these)

**Direct LI / order** (underscore tokens, 0-indexed):
`Newsweek_Direct_<vertical>_…_<holding>_<agency>_<advertiser>(7)_<campaign>(8)_<geo>_<format>(10)_<IO#>_Team-<USA|INTL>_<AE>`
- Vertical = token 2. Advertiser = 7, campaign = 8, format ≈ 10 (it drifts,
  e.g. token 11 on the AppleTV names). The seller is the last token, resolved
  through `settings.json → ae_names` (case variants included: `ILee` and
  `Ilee` both map to Ivy Lee).
- Example: `Newsweek_Direct_Tech_NA_NA_Omnicom_OMD_AppleTv_'Way-of-the-Warrior-Kid'-FY27-Q1_Display-Avail_US_Interstitial_SO01190_Team-USA_ILee`

**PMP deal names**:
`Newsweek_<PG|PD|PA|PMP>_<vertical>_<exchange>_<dsp>_<holding>(5)_<agency>(6)_<advertiser>(7)_<campaign>(8)_<geo>_<format>_$<floor>(11)_<team>_<ae>`
- The **floor lives in the name** (token 11, `$14`). SSP feeds don't carry
  per-deal floors. About 85% of deals parse.

**Formats** (7 canonical): Display, Video, Interstitial, Interscroller, FITO,
Centerstage, Apple News. GAM's `INVENTORY_FORMAT_NAME` flattens the house
formats into "Banner", so **name keywords win over the API** (precedence:
fito, apple-news, centerstage, interscroller/uniscroller, interstitial).
Uniscroller and Interscroller are the same product.

---

## 3. Reporting facts (v1 REST)

- Report flow: `create_report`, then `run_report` (a long-running op), then
  `fetch_report_result_rows`. The `DATE` dimension comes back as an int `YYYYMMDD`.
- **Time zones**: LI `start_time`/`end_time` are instants in ET. A flight
  ending 6/30 has `end_time = 2026-07-01T03:59Z`, so convert to ET before
  `.date()` (`gam_client._ts_to_date`). SOAP dates (`_soap_date_to_iso`) are fine.
- **Pull yesterday, not today.** Same-day data has latency.
- **Bid funnel (yield partners)**: `YIELD_GROUP_CALLOUTS` (what the UI calls
  "Ad requests"), then `_BIDS`, `_AUCTIONS_WON`, `_IMPRESSIONS`.
- **Incompatibilities** (`REPORT_ERROR_CONSTRAINTS_INCOMPATIBILITY`):
  - `HEADER_BIDDER_INTEGRATION_TYPE_NAME` with any `YIELD_GROUP_*` metric.
    Tell OB from Mediation through SOAP `YieldGroupService` instead (fields
    `yieldGroupId`/`yieldGroupName`).
  - `UNFILLED_IMPRESSIONS` with `INVENTORY_FORMAT_NAME`,
    `LINE_ITEM_ENVIRONMENT_TYPE_NAME` or `AD_REQUEST_SIZES`. Use
    `AD_UNIT_NAME_TOP_LEVEL` and `REQUESTED_AD_SIZES` instead.
  - `DEAL_ID` with the delivery metric set (it multiplies rows). The repo runs
    a separate `[LINE_ITEM_ID, DEAL_ID]` report (`run_li_deal_map_report`).
    `PROGRAMMATIC_DEAL_ID` isn't a valid v1 dimension.
- **GAM `DEAL_ID` = TTD `deal_id`** on our PG flights, which makes it the join
  key to TTD CPA data. Normalize away a `.0` float suffix.
- **Crossing two key-values**: `KEY_VALUES_NAME` splits one impression into a
  row per key. To cross two keys (e.g. `hb_bidder` × `hb_source`), bind
  `CUSTOM_DIMENSION_<n>_VALUE` through `custom_dimension_key_ids` (the key must
  be reportable as a custom dimension). Filter high-cardinality dimensions
  server-side.
- **Avails** (`scripts/gam_intl_avails.py`):
  - Use `avails = impressions + unfilled`. Ad requests is the upper bound,
    about 4–7% higher.
  - Use `AD_UNIT_NAME_TOP_LEVEL`, not `AD_UNIT_NAME` (that's the leaf name).
    `newsweek` is display and `vid.newsweek` is all video. `applenews.*` and
    `newsletter.*` aren't part of the site book.
  - `REQUESTED_AD_SIZES` is a size *set* per request, so **per-size avails
    overlap and must never be summed**.
  - `MONTH_YEAR` comes back as `(year-1900)*12 + (month-1)`.
- **Forecasts** (`scripts/gam_avails_forecast.py`, SOAP):
  - Quote `availableUnits`, not `matchedUnits`.
  - A video LI needs `environmentType=VIDEO_PLAYER`,
    `requestPlatformTargeting` and **`videoMaxDuration`**. The error message
    says `maxVideoCreativeDuration`, but that field doesn't exist.
  - A multi-placeholder LI forecasts the union of its sizes.
  - `ForecastingError.EXCEEDED_QUOTA` is the throttle. Keep workers at 3 or
    fewer, and record a dropped call as **missing, never 0**.
  - Join countries by ISO code.
  - Sanity-check against the trailing-28-day run-rate. GAM ran about 80% of
    it in Sep 2026.

---

## 4. Trafficking playbooks

### New Direct order from a signed IO
`scripts/setup_io_order.py` + `scripts/orders/<IO#>.json` (example: SO01190).
The workflow is `setup_io_order.yml`. **A push is always a dry run.** To write,
flip `APPLY_ON_PUSH` to `"true"` in its own commit, read the run, then flip it
back. The script:
- checks `qty × CPM == amount` before touching GAM
- looks up first and never duplicates an order or LI
- creates new LIs as DRAFT with no creatives.

Rules:
- **The IO drives type and goal**: a CPM buy with a quantity is **STANDARD,
  priority 8, LIFETIME impression goal = the IO quantity**. Targeting,
  placeholders and roadblocking are cloned from a prior flight's *paid*
  template LI, but **never its line type**. (The first SO01190 apply copied a
  SPONSORSHIP p4 template and lost the quantity.)
- PO field = the SO number. The client PO, campaign string and totals go in
  the order notes.
- Apple TV+/OMD runs under advertiser `[nw] Omnicom`, with no agency company.
  The template is LI 7330684240 (Cape Fear SO01090): a **2x1 PIXEL**
  placeholder, ONLY_ONE roadblocking, BROWSER.
- Creatives and order approval happen in the GAM UI.

### Third-party tags and creatives
- **Every third-party creative declares its ad tech** (`thirdPartyDataDeclaration`,
  DECLARED). The ids come from Google's ATP list
  (`https://storage.googleapis.com/adx-rtb-dictionaries/providers.csv`), not
  network Companies. **209 = Innovid/Flashtalking, 62 = comScore.** Declare
  every vendor that fires on the creative.
- **Interstitial creatives** on production orders carry the `interstitial`
  creative label **and** the Comscore pixel
  (`scripts/orders/pixels/comscore_interstitial.txt`, c2=6972086) as a
  `thirdPartyImpressionTrackingUrls` tracker, never spliced into the tag. That
  means declaring ATP 62. `scripts/attach_tag_to_order.py` does all of this
  automatically.
- **Out-of-page slots** need "Out of page"-size creatives, not 1x1. The LI
  placeholder is `creativeSizeType: INTERSTITIAL`. LIs on OOP units need
  `skipInventoryCheck` + `allowOverbook`.
- Native-style macros are `[%Var%]`. Bare `[Var]` isn't substituted.
- Inside a `<script>`, split `</script>` as `'</scr' + 'ipt>'`.

### Demo-gated test placements
The site's `?nwdemocr=<value>` URL param sets a same-named GPT key-value. Demo
LIs live on **Newsweek_Test-2 (4082002976)** targeting `nwdemocr=<value>`, so
real traffic never sees them. `scripts/setup_demo_creative.py` clones a demo
LI and attaches a tag. **The value is always the tag sheet's `Placement_ID`**
(the script enforces this). `scripts/inspect_line_item.py` (and its workflow)
dumps any LI's full setup and creative tags.

### Placement injection (ads anywhere in the article DOM)
See `docs/gam_placement_injection.md`. In short:
- A **carrier slot** (`inarticle1` or `interstitial`; `oop*` doesn't render on
  articles) plus a **priority-3 SPONSORSHIP LI**.
- A **SafeFrame-OFF wrapper CustomCreative** that hides its slot, anchors on a
  stable CSS substring, and writes the payload into a friendly iframe.
- A once-guard on a `window.__nw…` flag.
- Use **ONE_OR_MORE roadblocking** on multi-unit LIs. ONLY_ONE launched the
  Infiniti flight dark.

Live examples:
- Article sponsor logo: `oop1`, LI 7336465381 (`docs/article_sponsor_logo.md`).
- Apple FITO top banner: LI 7337440033 (`scripts/setup_fito_top_banner.py`).

### Insights native banners
See `docs/insights_native_ad.md`. The **live** styles are `Native (WxH)`
1014148 / 1014151 / 1014379 on template **12552841**, gated
`?nwdemocr=native`. The repo-created `12412102` set is archived. CSS edits
reach production only through `setup_insights_native_styles.py --style-ids …`
or the `push_insights_native_style.yml` workflow. Copy caps: TITLE 100 /
SUBTITLE 220 / HASHTAG 20.

### PMP / PD / PG housekeeping
- Archive a PD/PG through `GAMClient.archive_proposal_line_item(pli_id)`
  (`scripts/archive_pli.py`, `archive_pli.yml`).
- Pubmatic and Magnite have no publisher-side archive API, so archive there in
  their UIs.
- `export_gam_deals.yml` and `gam_gambling_deal_ids.yml` pull deal lists.

---

## 5. Viewability: the hard-won lessons

- **Active View measures the GPT slot iframe.** A creative that renders in the
  parent DOM (Mobkoi interscroller/uniscroller, takeover customs, injected
  placements) reads **~0% viewable at 100% measurable**. DV instruments the
  same element, so it agrees and is equally wrong. The tell: healthy CTR, more
  clicks than "viewable" impressions.
- **You can't declare viewability.** `%%VIEW_URL_UNESC%%` counts
  *impressions* for OOP creatives, not views (tested and null).
- **Fixes that work**:
  - The **Mobkoi iframe mirror**: a transparent absolute fill of the slot div
    (`docs/snippets/mobkoi_iframe_mirror_creative.html`,
    `apply_mobkoi_iframe_mirror.py`). It took viewability from 0.5% to about
    57%. Mirror only the iframe, never the GPT container.
  - **Carrier-reposition** for injected placements, sized to the iframe's
    **real height** (about 150px for OOP). An `overflow:hidden` clip to the
    visible element made AV see 16% and book every impression non-viewable.
- **Never mirror an ad that isn't shown.** Ogury on `dfp-ad-sticky` hides its
  iframe at 0×0 with nothing rendered, so it's correctly non-viewable. That's
  a delivery defect to raise with Ogury.
- **Prebid bidders at 40–56%** aren't the breakout signature, which floors at
  about 0% (`docs/prebid_viewability.md`). Diagnostics:
  `prebid_viewability_audit.py` and `prebid_render_forensics.py` (article pages only).
- **You can't self-test AV with headless traffic.** It's IVT-filtered (reads
  0) and pollutes live flights. Validate geometry with the carrier iframe's
  IntersectionObserver ratio, then wait for next-day organic AV.
- AV's large-creative threshold (over 242,500 px²) is 30% in view for 1s.
  Never set vCPM goals on breakout formats.

---

## 6. Recurring deliverables that run on GAM data

| Ask | How |
|---|---|
| Proof-of-placement screenshots | `pull_screenshots_source.yml`, then `capture_screenshots.yml`, then `scripts/build_screenshots_deck.py`, which outputs a **`.pptx`, one per advertiser**. Pages must match the client's vertical **and** read `brandsafe=y` from the page's own KV. In-context framed shots only. See `docs/screenshots_document.md`. |
| Comscore CCR setup form | `build_ccr_form.yml` with the order ids (commas = one form, spaces = one per order). Excludes Apple News, newsletter and canceled LIs. The Media Details partner breakdown is left blank. The template must have no hidden sheets except "Data Validation". |
| Delivery-issue outreach (e.g. Chumba/TTD goal sync) | `docs/seller_comms.md`: pull LI goals with `get_active_line_items()`, deal ids with `run_li_deal_map_report()` |
| Avails / forecast for a sales ask | `gam_intl_avails.yml` (historical) / `gam_avails_forecast.py` (future) |
| Confiant brand-safety blocks | `docs/confiant_blocklist.md`: daily push to Protection 28044902 (launchd on Roger's Mac) |

---

## 7. Dashboard plumbing that depends on GAM

- `refresh_cache.py` runs `refresh_gam` (delivery + lifetime + metadata +
  LI→deal map), the PMP deals report, and `gam_deal_bid_daily` (per-deal bid
  funnel: `deals_bid_requests` / `deals_bids`). The full sweep runs 09:00 UTC
  through `refresh.yml`, triggered by cron-job.org.
- Orders included: `Newsweek_Direct%`, `Newsweek_PG%`. Excluded: the two test
  orders above.
- `pmp_last_bid_date` tracks deal bid recency cumulatively (GAM key =
  `programmatic_deal_name`).
- The health check (`health_check.yml`) watches freshness and DV↔GAM join rate.

---

## 8. Owner preferences to keep honoring

- Never push to `main`. Always use a branch and a PR, even for docs. Keep docs
  in the same PR as the code they describe.
- GAM writes are **dry-run by default**, lookup-first and idempotent. Show the
  dry run before `--apply`.
- Ask before mirroring a template LI's line type.
- A screenshots deliverable is always a `.pptx`. Demo gating values are always
  the `Placement_ID`.
- Interstitials always get the `interstitial` label plus the Comscore pixel.
- Use they/them for people whose pronouns aren't stated. Sellers come from
  `ae_names` in `settings.json`.

## 9. Where to read deeper

Sibling packets: `docs/ccr_handover_packet.md` (Comscore CCR forms) ·
`docs/screenshots_handover_packet.md` (proof-of-placement decks).

`CLAUDE.md` (the "GAM facts" section is the canonical list) ·
`docs/gam_placement_injection.md` · `docs/article_sponsor_logo.md` ·
`docs/insights_native_ad.md` · `docs/mobkoi_viewability.md` ·
`docs/prebid_viewability.md` · `docs/screenshots_document.md` ·
`docs/seller_comms.md` · `docs/confiant_blocklist.md` · `docs/changelog.md`
(dated, keyed by PR) · `gam_client.py` (all REST + SOAP entry points).
