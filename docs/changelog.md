# Changelog

Chronological record of shipped work. Durable "how it works" detail lives in
`CLAUDE.md` (the feature/design sections); this file is the "what changed when,
and why" index, keyed by PR. Newest first.

## 2026-06-22 — Editorial landing polish

- **Per-LI CPA in the Direct drawer.** The Direct LI drawer now shows a **CPA
  acquisition** block — CPA, conversions, and a **daily-CPA chart** — for the
  gambling LIs that map to a TTD ad_group. The TTD feed has no GAM
  `line_item_id` and `gam_campaigns` has no TTD `deal_id`, so the join is on the
  two dimensions both names encode — **audience (Casino/Social) + ad size**
  (`dl.cpa_join_key` → `"casino|728x90-300x250"`, matching the TTD ad_group and
  the GAM LI name alike). `dl.ttd_cpa_for_li` aggregates that ad_group's rows
  from the LI's `start_date`. Only the ~8 gambling LIs match (every other LI's
  key is None → no block); `_ttd_trend_svg` was hoisted so the drawer reuses the
  card chart. Verified on prod (e.g. Luckyland 728x90-300x250 Casino → CPA
  $174.02 / 26 conv in June). Pinned by `test_cpa_join_key` / `test_ttd_cpa_for_li`.
- **TTD cards: date window follows the Status filter + ad-size breakdown.** Each
  Luckyland / Chumba card now windows to **`start` = the earliest `start_date`
  among that campaign's GAM LIs that pass the active filters** (`_ttd_li_start`
  reads the already-filtered `view_gam` by `order_name` token;
  `ttd_cpa_summary` gained `start`/`end`). So the cards follow the dashboard's
  **Status filter**: with "Delivering" selected, only the active LIs count — and
  since those started this month, last month's now-*Completed* flight drops out;
  include Completed and the window extends back. (Went through current-month and
  flight-to-date on the way; this filter-driven version is the one that actually
  matches the Direct campaigns. The orders are `Newsweek_PG_…`, included in the
  Direct view via `included_order_patterns`.) And when a card is opened it now
  shows a **by-ad-size** breakdown
  (`by_ad_size`) above the by-format table. Ad size is **parsed as a `WxH` token
  from the `creative` name** — the TTD tables have no `creative_size` column, so
  the first cut grouped on a column that didn't exist and showed nothing; size
  actually lives in the creative string (`…_DisplayBanner_300x250_May_…`). Video
  creatives (a duration, no pixel size) drop out. Logic in `dashboard_logic`,
  pinned by `test_ttd_cpa_summary_*` (67/67 pass).
- **TTD CPA cards → "Editorial scorecard."** The expanded Luckyland / Chumba
  priority-flight views were a 5-equal-tile row + two horizontal **bar-lists**
  (one row per day) + a media table — the one spot still reading like a raw
  export. Reworked (`_render_ttd_cpa`) to a **CPA hero** figure (the campaign's
  optimization target) + a quiet 4-stat grid (Conversions / Spend / Conv. rate /
  Clicks), two **SVG trend charts** (`_ttd_trend_svg` — area = daily
  conversions, line = daily CPA, uniform regime so the end-dot stays round),
  then the media-type table. Chosen from a 3-direction mock
  (`docs/ttd_card_options.html`); presentation only, same `summary` data.
- **#297** — **CTR card sits next to VCR on desktop.** The nine KPI tiles were a
  wrapping flex row, so CTR orphaned onto a second line. On ≥1025px the band is
  now a deterministic 11-column grid (heroes span 2, the 7 quality tiles 1 each)
  — one row, no wrap. Below 1025px keeps the wrapping flex.
- **#296** — **Round KPI sparkline dots.** The tile sparklines used the stretch
  regime (`preserveAspectRatio="none"`), which elongated the round end-cap into a
  dash on iOS Safari. Switched all nine tiles to the uniform regime
  (`.kpi-spark` → `height:auto`) so x/y scale equally and the dot is a true
  circle. (#295 first made the tiles consistent; #296 fixed the actual stretch.)

## 2026-06-21 — Campaigns "Editorial" landing

- **#294** — **Campaigns landing redesigned to the "Editorial" layout; the
  sticky Cockpit rail is removed.** The fixed right rail (#275) was
  `position:fixed` and **overlapped the KPI strip** — it clipped the VCR tile
  and hid the 9th KPI (CTR) at common window widths. The redesign fixes that
  *structurally* (nothing is fixed-positioned anymore) and gives the page a
  clear first read. New top-to-bottom order: (1) a **"Needs you today" briefing
  lede** (`.nw-brief`) — the Needs-attention categories (`_na_cats`, unchanged)
  in normal flow, a compact tap-to-expand auto-fit grid on desktop; (2) the KPI
  metrics **kept as cards but tiered** (`.nw-kpi-cards`) — Revenue · Avg pacing
  as **double-width hero tiles** + the other seven QA metrics as standard tiles,
  one wrapping flex row, replacing the flat **9-up `.nw-kpi-row`** equal grid
  (an interim borderless hero+hairline band was reverted on Roger's "we must
  keep the cards"); (3) **Priority flights** — the two TTD CPA cards demoted +
  collapsed (`.nw-na--collapsible`, opting out of the desktop force-open).
  **PMP signals** moved from the rail into the PMP section's normal flow
  (`_pmp_sig_slot = st.empty()`). Same values / subtitles / series throughout —
  **only presentation changed; all decision logic untouched.** Chosen from a
  **5-direction mock** (`docs/campaigns_redesign_options.html` — Editorial /
  Cockpit / Status board / Split / Tiles 2.0; Roger picked **Editorial**).
- **#294** (proposal) — **5-direction redesign mock** for the Campaigns landing
  (`docs/campaigns_redesign_options.html`), a self-contained HTML file on
  production tokens used to choose the direction before writing code.

## 2026-06-17 — Campaigns Cockpit

- **#275** — **Campaigns desktop "Cockpit": sticky right rail.** The unified
  Needs-attention triage card **and** the PMP-signals card now render into a
  keyed `st.container(key="nw_campaigns_rail")` that desktop CSS
  (`@media min-width:1025px`) pins as a **fixed top-right rail**; the main
  `.block-container` is shrunk (`max-width:min(1320px, 100vw-380px)`) with a
  reserved right gutter (`margin-right`) so content + rail sit side by side
  without overlap. ≤1024px nothing applies — the cards stay in normal flow above
  the KPIs, unchanged. So Campaigns reads as **left = work (KPIs + tables),
  right = always-visible triage**, which declutters the old vertical stack and
  caps the previously full-width (stretched) content. Additive — no control-flow
  change; reuses the existing block-container selector group so the override wins
  on source order. Chosen from a 3-way mockup (Focus / Cockpit / Command; Roger
  picked Cockpit). Built + visually QA'd locally on synthetic data (no prod) via
  the new `scripts/seed_local_demo.py`, which fabricates a throwaway SQLite DB
  with the tables the Campaigns view reads (DV tables fall back to empty on
  SQLite → Attention/SIVT/GIVT show "—"). 56/56 tests pass.
- **#274** — **Needs-attention card stays open on mobile.** First slice of the
  Campaigns desktop **"Cockpit"** rework (main work area + sticky right rail —
  Roger's pick from a 3-way Focus / Cockpit / Command mockup). New
  `nw-na--always` modifier forces the card body open, hides the chevron, and
  makes the header non-interactive at **all** widths (paired with the `open`
  attribute), reversing the 2026-06-14 mobile collapse. The reason for that
  collapse (the card "dominating the first screen above the KPIs") is avoided
  instead by keeping only the **~4 category rows** open while each category's
  line-item list stays independently tap-to-expand. Scoped to Needs-attention;
  the **ending-soon** and **PMP-signals** cards keep the default mobile collapse.
  Pure CSS + markup; decision logic untouched (55/55 logic tests pass). The
  desktop **sticky rail + grouped/banded table are deferred** — they need a
  running instance to build against (the Campaigns tab is one ~3,000-line
  sequential scroll, so a side-by-side rail is a large `with main_col:` reindent
  whose visual result can't be verified in CI).

## 2026-06-15 — Direct table polish

- **#261** — **Hotfix: `NameError` in `_pmp_airtable_url`** (crashed the PMP tab,
  Roger's screenshot). #260's floor refactor removed the `_dt = row.get("Deal
  Type")` line from `_pmp_airtable_url` when swapping in `_deal_floor(row)`, but
  `_dt` is still used two lines down in the eCPM-vs-floor `notes` string —
  undefined-name at runtime. Restored the line. (The crash is render-code only,
  so `py_compile` + the logic tests passed; it also surfaced *more* after #260
  because the per-deal floors populate ~85% of deals, so far more rows now reach
  the floor-thesis branch.) Verified `pyflakes` reports 0 undefined names across
  `dashboard.py`; 120/120 tests pass.
- **#260** — PMP **Configured floor now comes from the deal name**, not just the
  per-deal-type settings floor (Roger: "are you not able to bring the configured
  floor from the SSPs?"). The SSP delivery feeds don't carry a per-deal floor
  (Pubmatic/Magnite none; GAM only for PA deals, unjoinable to revenue) — but
  Newsweek embeds it in the deal name as token 11 (`…_$14_…`), the same way
  DSP / advertiser / campaign / format are already derived. New
  `dl.pmp_deal_floor(name)` parses the `$<floor>` token; a `_deal_floor(row)`
  helper resolves **name floor first, settings per-type floor as fallback**.
  Wired into all four floor surfaces: the drawer's Configured-floor cell, the
  eCPM-vs-floor status banner, the floor-breach exception banner (vectorized),
  and the mobile card's eCPM-vs-floor bar — so the banding works **per-deal**
  instead of per-type. The Google Evergreen PD deal that read "—" now shows
  `$14.00`. Prod coverage: 229/271 (~85%) of distinct deals carry a parseable
  floor token; the rest fall back. New `test_pmp_deal_floor` (real prod-shaped
  names); 120/120 tests pass.
- **#259** — Direct drawer: **Creative duration cell shows on video lines only**
  (Roger). A creative's duration is only meaningful for video, so the
  `.nw-li-grid` "Creative duration" cell is now gated on `_is_video` (format
  contains "video"); non-video LIs drop it and show 6 detail cells instead of 7.
  Render code only; 119/119 tests pass.
- **#258** — Direct drawer: **video lines now show a CTR card alongside VCR**
  (Roger flagged the CTR card "missing" for video). The drawer's second
  small-multiple used to be VCR *instead of* CTR for video (`second_label =
  "VCR" if is_video else "CTR"`), so video lines never showed CTR. Now CTR is
  always shown and VCR is added for video, so a **video line shows 6 cards**
  (Viewability · VCR · CTR · Attention · SIVT · GIVT) and non-video shows 5. A
  new `.nw-sm-grid--6` modifier widens the desktop row from `repeat(5,1fr)` to
  `repeat(6,1fr)` so the 6 cards stay in one aligned row; mobile keeps the
  2-col default (3 rows). Each panel still skips when its series is empty (a
  video line with no daily completion data simply shows no VCR card). Render
  code only; 119/119 tests pass; verified with desktop (row of 6) + 390px
  (2-col) renders.
- **#257** — Direct drawer: **the LI-name title is now a `<div>`, not an
  `<h3>`** (mobile font-size fix). Streamlit styles markdown headings via
  container-scoped selectors that outrank a bare class, so the
  `<h3 class="nw-li-name">` rendered at Streamlit's heading size (~24px) instead
  of the 13px set in #256 — and the long full GAM name wrapped to ~8 lines,
  dominating the mobile screen (Roger's screenshot). The standalone render mocks
  missed it (no Streamlit CSS). Switching the title to a `<div>` means the
  heading selectors no longer match, so `.nw-li-name` (13px mono) applies.
  Proven with a mock that simulates Streamlit's `h3` rule (h3 → huge, div →
  13px). One-line HTML change; 119/119 tests pass.
- **#256** — Direct drawer spec-card cleanup (from #255):
  - **One name, and it's the full GAM line-item name.** The friendly serif
    title + the raw mono string stacked read as "two names for the LI" (Roger's
    screenshot) — redundant. The card now shows a single title, and per Roger
    it's the **full GAM line-item name** (`.nw-li-name`, rendered **mono** since
    it's a structured technical identifier) — not the friendly
    `<Advertiser> — <Campaign>` derivation; the detail view shows the real
    complete GAM name, while the **table rows keep the friendly name** (scannable
    + the A–Z sort key). The friendly name's useful parts (Format / CPM / Seller)
    are decoded into the grid, and the GAM-ID pill is the deep link.
  - **Dropped the `Status` detail-grid cell.** Redundant with the top pacing
    banner (`✓ On track` / `⚠ Underpacing` / …), which the drawer still leads
    with and which conveys delivery state at a glance. Removed the cell, its
    `_status_v`/`_status_ok` vars, and the now-unused `.nw-li-grid .v.ok` CSS.
    The banner (incl. red/amber alerts) is unchanged.
  - CSS + `_drawer_html` only; 119/119 tests pass.
- **#255** — Direct drawer: **consolidated the LI name + metadata into one spec
  card after the graphs** (`_drawer_html` → `.nw-li-card`). The drawer used to
  open with the raw LI name in a mono box at the top, then dump a flat 9-cell
  meta grid at the bottom whose `Order` field repeated that same raw name — the
  name appeared twice and the metadata read as an afterthought ("thrown in after
  all the graphs", Roger's screenshot). Now one card below the charts leads with
  the friendly `<Advertiser> — <Campaign>` title (serif) + a GAM-ID pill, the
  raw convention string as a mono caption (replacing the duplicate `Order`
  field), then 3 hero pacing tiles (Goal / Delivered + progress bar / Remaining,
  compact K/M serif figures) over a tinted detail grid (Flight · Status ·
  Format · CPM · Revenue · Clicks · Seller · Creative duration). Adds Delivered
  + Revenue (weren't shown) so the card is self-contained; the orphaned
  "Creative duration —" now lives in the grid. Chosen from a 3-option visual
  mock (spec sheet / definition list / **hero tiles** — Roger picked hero
  tiles). PMP drawer unchanged. CSS + `_drawer_html` only; 119/119 tests pass;
  verified with real-CSS renders at 1280px + 390px.
- **#254** — PMP deal drawer: on **desktop** the 3 trend charts now read as a
  **headline + funnel row** — revenue spans the **full drawer width** on top,
  with **total requests + bid responses paired in a row directly below it** —
  instead of a tall 3-high full-width stack that left the drawer's right half
  empty (Roger's screenshot; same "improve it like the Direct drawer" intent as
  #252/#253). The three charts wrap in a new `.nw-pmp-charts` flex container
  (`@media min-width:1025px`): `:first-child` (revenue) is forced full-width via
  `flex-basis:100%`, the rest share the next flex line at `flex:1 1 240px`. The
  variable chart count rides the flex with no builder branch — a 2-chart deal
  (Pubmatic: revenue + bid responses) shows revenue full + responses full below;
  a revenue-only deal shows one full-width chart. Mobile (≤1024px) is untouched
  — the wrapper is a plain block, so every chart stacks full-width as before.
  CSS-only (+ a one-line wrap of the three charts in `_pmp_drawer_html`).
  Verified with real-CSS renders at 1400px (GAM 3-chart + Pubmatic 2-chart) and
  a true 390px viewport (stacked). 119/119 tests pass.
- **#253** — Direct drawer alignment fix, **superseding #252's side-by-side**.
  On **desktop** the 7-day delivery chart now spans the **full drawer width**
  and the Viewability / CTR / Attention / SIVT / GIVT small-multiples sit in
  **one aligned row of 5 directly below it** (`.nw-drawer-charts > .nw-drawer-chart`
  and `> .nw-sm-grid` both drop their 760 cap to `max-width:none`, and the grid's
  `grid-template-columns` becomes `repeat(5,1fr)` at ≥1025px). #252's flex
  side-by-side left the short chart next to a 3-row 2-col grid, which read ragged
  / unaligned (Roger: "the graphs not aligned"). Now both edges line up,
  full-bleed. Mobile (≤1024px) is unchanged — capped chart + 2-col grid, stacked.
  CSS-only; verified with a real-CSS render at 1400px (aligned row of 5) + 390px
  (still 2-col stacked). 119/119 tests pass.
- **#252** — Direct drawer: on **desktop**, the Viewability / CTR / Attention /
  SIVT / GIVT small-multiples lift up **beside the 7-day delivery chart** (a
  new `.nw-drawer-charts` flex row, ≥1025px) instead of stacking below it and
  leaving the drawer's right half empty (Roger's screenshot). The chart holds
  ~760px on the left; the grid fills the right. Mobile (≤1024px) still stacks.
  CSS-only; verified with a real-CSS render at 1400px + 390px. **Superseded by
  #253** — the side-by-side read ragged; replaced with full-width chart + a row
  of 5 below.
- **#250** — Badge numbering reverted to **per GAM order** (from #248's
  per-displayed-campaign-group, which left unique campaigns badge-free — most
  Infiniti/Jeep lines lost their `#`, which Roger flagged). Now every line of a
  multi-line order is numbered `#1..#N`, but the `cumcount` runs **after** the
  A–Z sort so it follows campaign-alphabetical order, **not** `line_item_id` —
  keeping the low→high reading #248 was after without dropping any badges.
  Proven on the real 29-LI Infiniti order: `#1..#29`, monotonic, no scatter.
- **#249** — **Exclude two test/QA GAM orders from the Direct view**
  (`_EXCLUDED_ORDER_IDS`): `3648897741` (GMC "Terrain Diverse Owned TEST PAGE" /
  CITIQ3 — 386 LIs, no `order_name`) and `4082002976` ("Newsweek_Test-2" — the
  `[TEST]` Newsletter / Apple-FITO / Sponsor-Logo batch, 30 LIs). Filtered on
  `order_id` right after `gam_df` loads, so all 416 test LIs drop out of the
  table, KPIs, and DV joins. (Roger first gave `3648897841` — a transposed
  digit that matched no rows; corrected to `…741` after confirming.)
- **#248** — Direct line-item **`#N` badges now number per displayed campaign
  group** (ascending by `line_item_id`) instead of per GAM `order_name`. The
  per-order numbering scattered one order's 1..N across its different campaign
  names once the table sorted A–Z by display name — `#6` sat above `#3/#4/#5`,
  and single distinct campaigns showed high numbers (Roger's screenshot). Now
  each campaign group reads `#1, #2, #3…` low→high and a single-LI campaign
  shows no badge; the table sorts A–Z by display name with `line_item_id` as
  the tiebreak. Folded the ordinal + sort blocks into one (single display-name
  derivation). 119/119 tests pass; new numbering simulated on screenshot-shaped
  data.

## 2026-06-14 → 06-15 — Load-time + PMP drawer & mobile polish

Two intertwined threads. **(1) Cold-load speed** — first paint was dominated by
the two big DoubleVerify tables (~17 MB across `dv_attention` + `dv_ivt`) plus
repeated per-render work; fixed by memoized aggregates (#239), `load()` column
projection + vectorized Direct rate cells (#240), and finally server-side
pre-aggregation of DV (#247). **(2) PMP drawer / table / mobile** — pagination
(#241), the compact one-row pager (#243), the hidden-deal subtitle (#242),
tap-to-drawer on the signals card (#244), the bid-funnel drawer charts
(#245/#246). All squash-merged to `main` on green (119 tests).

- **#239** — Memoize the DV aggregations: the per-LI / per-order Attention and
  MRC SIVT/GIVT rollups moved into two `@st.cache_data` helpers
  (`_dv_attention_aggregates` / `_dv_ivt_aggregates`), so the groupbys run
  once per cache period instead of on every interaction. Byte-identical to the
  old inline logic (same `dl.*` calls, dicts default empty when a table is
  absent). Speeds clicking around, not the cold load.
- **#240** — Three cold-load + render wins:
  - **Column projection in `load()`** — `dv_attention` / `dv_ivt` now SELECT
    only the consumed columns (5 of 15 / 6 of 14), dropping the 8 unused
    attention indices, the precomputed IVT rates the dashboard recomputes from
    `monitored_ads`, and metadata. Cuts cold-load wire bytes **~56–60%** on the
    two tables that dominate first paint (measured 6.3→2.5 MB + 11→5.2 MB,
    ~9.5 MB saved). A projected SELECT that errors (schema drift) falls back to
    `SELECT *`, so it's a pure optimization; `_COL_PROJECT` must stay in sync
    with every DV consumer (CLAUDE.md gotcha).
  - **Vectorize the per-day rate columns** — the Direct table's
    viewability / CTR / VCR `_1d`/`_2d` rates were six per-row `.apply(axis=1)`
    passes (each builds a `pd.Series` per row); now column math
    (`(_num/_den).where(_den>0, None)*100`), mirroring the lifetime-rate
    pattern already in the function. The only consumer (`_fmt_pct_annot`)
    guards with `pd.isna`, so the NaN-vs-None change is invisible. Proven
    behaviour-identical: 0 mismatches on 5,010 synthetic edge-case rows and 0
    divergent rows on live `gam_campaigns` (where 1,782 null + 21 zero
    denominators actually occur).
  - **Memoize `dl.line_item_display_name`** (`@lru_cache`) — the Direct table
    derives each LI name twice (sort key + render); it now parses once,
    matching the `_parse_deal` convention from #236.
- **#241** — **Paginate the Direct campaigns table** at 25 LIs/page with the
  same `← Prev / Page X of N / Next →` control (top + bottom) the PMP table
  already uses. The Direct table previously rendered *every* filtered line item
  into one custom-HTML DOM per rerun (thousands in cache); it now builds 25 rows
  per page. Positional `.iloc` slicing preserves index labels so the per-row
  viewability / CTR lookups (`index.get_loc`) still resolve; the page resets to
  0 on any filter change (`_direct_filter_sig`) and clamps to range. Mirrors the
  PMP pager (`pmp_page` → `direct_page`); pinned by an in-isolation slice/clamp
  simulation (every row tiles exactly once across 1…3,798 rows).
- **#242** — PMP table subtitle now explains the hidden-deal gap. With the
  default "Show deals under $100/day" filter off, the header read a bare
  `N of M shown` (e.g. "10 of 277"), which looked like missing data; it now
  reads `N of M shown · K under $100/day hidden`. The whole gap *is* that one
  revenue threshold — it's the only row filter between `_pmp_count` and
  `_pmp_display` — so the label is always exact.
- **#244** — **PMP signals deals → tap for the full drawer.** Each deal inside
  Spend momentum / No delivery / Stale deals now expands to the same detail
  panel the main PMP table row opens — yield banner, 7-day revenue chart, bid
  metrics, metadata grid — for delivering deals (matched in the unfiltered
  combined frame by `Deal` name); no-delivery / long-stale deals (no perf data)
  expand to a setup grid (status/floor/dates or SSP/last-bid/first-seen). The
  signals card now renders into an `st.empty()` slot under the KPI strip but is
  built by a deferred `_render_pmp_signals()` called after `_pmp_drawer_html` is
  defined, so the deal rows can reuse the table's drawer without moving it;
  `_sp_rows_for` gained an optional `wrap` callback for the momentum rows.
- **#245** — **Two more drawer charts: Total requests + Bid responses.** The
  PMP deal drawer now shows the 7-day bid-funnel trend next to revenue.
  `_pmp_drawer_revenue_chart` generalized to `_pmp_drawer_trend_chart(series,
  dates, label, money)` (K/M formatting, `$` only when `money`); `_pmp_daily`
  carries `total_requests` / `bid_responses` (Magnite `bid_requests` /
  `bid_responses`, Pubmatic `total_requests` / `non_zero_bid_responses`), and
  `dl.revenue_daily_series_by_deal` generalized to
  `dl.daily_series_by_deal(df, value_col)` (revenue kept as a thin wrapper, test
  unchanged). Each chart skips when its metric sums to ≤0, so GAM deals (no bid
  funnel) show revenue only and Pubmatic shows revenue + responses (its
  `total_requests` is empty upstream). Magnite carries the funnel for 179 deals.
- **#246** — **GAM bid funnel was wrong in #245 — corrected.** The assumption
  that GAM has no per-deal request/response data was false: it lives in a
  separate table, `gam_deal_bid_daily` (`deals_bid_requests` / `deals_bids`),
  keyed by `programmatic_deal_name` — the same Deal key, so it merges with the
  GAM revenue rows on `(ssp, deal, date)`. #245 only sourced GAM from
  `gam_pmp_deals` (impressions/revenue), so GAM deals wrongly showed revenue
  only. Now **45 delivering GAM deals** also show the Total requests + Bid
  responses charts. All three SSPs report the funnel.
- **#247** — **Server-side pre-aggregation of the DV tables.** The Campaigns
  view no longer loads `dv_attention` / `dv_ivt` raw (~68k rows, the dominant
  cold-load cost); it reads `GROUP BY` rollups computed in Postgres
  (`_load_dv_attention_agg` / `_load_dv_ivt_agg`, like `_load_li_max_duration`):
  per-(LI,date) + per-(order,date) + per-date Attention AVGs, and one
  per-(LI,order,date,validity) IVT `monitored_ads` SUM. ~42% fewer rows
  (24k→14k, 44k→25k) and the raw frames are no longer held. The grain *is* each
  `dl` aggregator's first-level reduction, so the **unchanged** `dl` functions
  produce identical output — proven on prod (a real-order test through the `dl`
  functions, 0 diffs; the attention order path checked across all 107 multi-LI
  order-dates, 0 divergent). Correctness: attention means don't compose so the
  per-order path gets its own query; IVT sums compose so one frame serves every
  path. Honest payoff: ~3 MB off cold load (~10% of first paint), not the "4–7×"
  first estimated — the raw rows were only ~1.7× inflated by duplicate creatives.
- **#243** — **Compact one-row pager** (`_compact_pager`) for both the Direct
  and PMP tables: `‹` · centered *Page X of N* (+ muted "N of M shown") · `›`.
  Replaces the `st.columns([1,4,1])` + full-width buttons, which **stacked into
  three full-width blocks on mobile** (Roger flagged it as bulky — shown a 4-way
  mockup, picked the compact bar). One shared helper wraps the arrows + caption
  in a keyed `st.container(horizontal=True)` (inline on mobile, same trick as the
  filter bars); `.st-key-nwpgrwrap_*` CSS pins the arrows to the edges of a
  430px-capped centered bar. Page logic/state untouched; desktop also gets the
  tighter bar. Verified by rendering the real CSS against a Streamlit-shaped DOM.

## 2026-06-13 → 2026-06-14 — PMP deals tab revamp + mobile polish

A two-day push reworking the **Campaigns → PMP deals** experience (readable
identities, a 7-day revenue trend, a unified signals card, seller-organized
no-delivery triage, week-over-week spend momentum) plus assorted Direct-table
and mobile-card fixes. All squash-merged to `main` on green (118 tests);
production redeploys from `main`.

### Identity — readable names everywhere
- **#219** — Direct row name → `Advertiser — Campaign` (name tokens 7+8), format
  dropped to the canonical chip (it was collapsing 34 Infiniti LIs into one
  "Infiniti - Display"). Also: a "PACE" label on the mobile pace bar, and a
  stretch-regime sparkline `overflow:visible` fix so the end dot stops clipping.
- **#220** — PMP deal name → `Advertiser — Campaign` + agency·holding subline
  (same token positions as Direct), via `dl.pmp_deal_display_name`. SSP-native /
  non-convention names returned whole, lightly cleaned.

### PMP revenue trend
- **#221** — 7-day per-deal revenue: drawer chart + mobile-card sparkline
  (`dl.revenue_daily_series_by_deal`, keyed `(ssp, deal)`). Helpers are
  PMP-local (the Direct `_sparkline_svg` is unreachable when GAM data is empty).
- **#222** — `_pmp_spark_svg` → uniform scaling (the PMP card box is ~9:1; the
  Direct stretch regime smeared the round end-cap on iOS Safari).
- **#223** — Mobile PMP card: deal-type pill pinned **top-right** (it was
  scattering inline after variable-length names).

### Stale deals
- **#217 / #218** — Archive-capability diagnostic when no button shows; the GAM
  fallback became a real `Archive in GAM ↗` link-button.
- **#225** — Hide deals that stopped being reported (paused/removed): added
  `pmp_last_bid_date.last_seen_date` (last day seen in ANY source) +
  `dl.recently_seen_mask`. Stale = no bids 90d **and** still seen within 90d.
  One-time additive prod migration (1,853 rows seeded).

### Needs-attention card
- **#224** — Collapse to one line on mobile (it dominated the first screen) +
  two-tier identifiable labels (advertiser bold over muted campaign) so sibling
  LIs are distinguishable.

### Stale deals
- **#237** — Folded the standalone "⚠ N stale PMP deals" expander into the **PMP
  signals** accordion as a read-only 3rd row (amber): `Advertiser — Campaign` +
  SSP · last bid · days-idle. The **Archive action was removed** ("no longer
  needed") along with its creds-gating helpers, the secret diagnostic, and the
  `.nw-stale-*` CSS (~156 lines) — so the row is static HTML and fits the
  accordion. Backend archive (`GAMClient.archive_proposal_line_item`,
  `scripts/archive_pli.py`, `archive_pli.yml`) kept.

### Spend momentum → PMP signals
- **#226 / #227** — Spend-momentum list: identifiable two-tier names + mobile
  layout; then one combined GAM+Magnite+Pubmatic PD+PA list (no SSP buckets,
  PMP-only), filtered to `|Δ| > $100`, sorted by recent revenue.
- **#228** — Folded spend-momentum + no-delivery into one **"PMP signals"**
  accordion under the PMP KPIs (default-open; reuses the Needs-attention CSS).
- **#229** — **Week-vs-week momentum**: widened the three PMP daily sources to
  **14 days** with a **per-report retention** refactor (`refresh_one_report`
  gains `window_days` + `retention_days`; invariant `retention = pull + 1`), and
  pinned the PMP summary to 7 days (`dl.window_last_n_days`) so its totals don't
  move. `_sp_date_momentum` extracted to the tested `dl.spend_momentum`
  (adaptive 7-vs-7). Retention proven in a Supabase temp-table sim (20 daily
  runs: 14 days / 0 dup rows; the old shared cutoff would have accumulated 33
  days / 276 dups) and verified live on a manual sweep.

### PMP filters
- **#230** — Deal Type defaults to `PA / PD / PMP` (PG excluded on load, via the
  existing filter + chip); "Show deals under $100/day" moved into the popover;
  PMP-signals accordion default-open.

### No-delivery triage
- **#231** — Drop the deals that are actually delivering, group by status,
  render readable `Advertiser — Campaign` names (was raw, truncated convention
  strings in a scroll table).
- **#232** — Group by **seller** (AE via settings.json `ae_names`); per-card
  **days-inactive** (last bid from `pmp_last_bid_date`, else `create_time`;
  colored by `dl.idle_band`); **PA/PD pill** top-right; exclude canceled +
  open-auction backstop deals.
- **#238** — Seller grouping was hard to read (a faint header, then a 18-deal
  seller buried the rest). Each seller is now a **collapsible row** (`.nd-sg`)
  — initials avatar + name + `count · worst-Nd` — deals nested inside,
  collapsed by default. Scan the sellers, drill into one.

### Performance
- **#236** — PMP table load: `_parse_deal` returned a **`pd.Series`** (~280µs/call,
  377× a dict) and ran per row across ~14 `.apply` sites (3× per row in the
  Magnite block), un-memoized — so the same deal name re-parsed on every one of
  its ~14 daily rows. The 14-day widening (#229) doubled the row counts and
  exposed it. Fix: return a **dict** + **`@lru_cache`**. Proven behaviour-identical
  on all 1,590 prod deal names (0 field mismatches); the per-row parse pattern
  (22k calls) drops **6,197 ms → 8 ms (767×)**.

### Direct pace cell
- **#233** — Box on-pace too (new quiet `.pill-green`), so every pace state is a
  pill; on-pace stays one tier below the loud amber/red so healthy still
  recedes (green-overwhelm rule preserved). Scoped to pacing — the shared
  `.txt-green` (viewability, CTR/VCR) is untouched.
- **#235** — "new line item" is now **existence-based** (`dl.is_new_line_item`):
  shown when a line didn't exist the prior day (first delivery is the latest
  day, `lifetime == impressions_1d`), not from a >100pp pace swing. So a genuine
  >100pp jump on an established line shows the real Δ; only just-launched lines
  read "new line item" (2 of 3,606 on current prod data). Pace delta passes
  `new_line_threshold=None`.
