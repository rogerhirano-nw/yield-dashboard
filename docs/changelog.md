# Changelog

Chronological record of shipped work. Durable "how it works" detail lives in
`CLAUDE.md` (the feature/design sections); this file is the "what changed when,
and why" index, keyed by PR. Newest first.

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
