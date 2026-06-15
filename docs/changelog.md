# Changelog

Chronological record of shipped work. Durable "how it works" detail lives in
`CLAUDE.md` (the feature/design sections); this file is the "what changed when,
and why" index, keyed by PR. Newest first.

## 2026-06-15 — Direct table badge/sort fix

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

## 2026-06-14 — Dashboard load-time optimization

A focused pass on the **Campaigns view's cold-load and interaction speed**,
after diagnosing first paint as dominated by the two big DoubleVerify tables
(~17 MB across `dv_attention` + `dv_ivt`) plus repeated per-render work. All
squash-merged to `main` on green (119 tests).

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
