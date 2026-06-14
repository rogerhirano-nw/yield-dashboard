# Changelog

Chronological record of shipped work. Durable "how it works" detail lives in
`CLAUDE.md` (the feature/design sections); this file is the "what changed when,
and why" index, keyed by PR. Newest first.

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
