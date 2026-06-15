# Claude Code notes for yield-dashboard

See `README.md` for project overview, files, and quickstart.
See `docs/changelog.md` for the dated "what changed when, and why" index (keyed by PR).

## Conventions
- Python (Streamlit dashboard + per-source clients). Cache layer is SQLite locally, Postgres in prod (`DATABASE_URL` Supabase).
- One client module per data source (`*_client.py`), one `refresh_<source>` function in `refresh_cache.py`, called from `main()`.
- Pull yesterday's data, not today's — same-day data has latency.
- **Hot per-row helpers return dicts, not `pd.Series`, and memoize.**
  `_parse_deal` (dashboard.py) runs per row across ~14 `.apply` sites in the
  PMP path. A `pd.Series` return cost ~280µs/call vs ~1µs for a dict (377×);
  it's now a **dict** + **`@lru_cache`** (the same deal name repeats across its
  ~14 daily rows — parse once). **Don't revert it to a Series.** The 14-day
  source widening (#229) doubled the row counts and made this the PMP table's
  dominant load cost (6.2s → 8ms for the parse pass; #236).
- **Never push directly to `main`.** Branch protection enforces PRs for everyone including admins. Always work on a branch and open a PR — even for docs-only changes. README/CLAUDE.md updates go in the same PR as the code they describe.
- **Dashboard testability rule: dashboard.py renders, `dashboard_logic.py`
  decides.** Decision logic — format classification, benchmark thresholds
  and banding, DV/IVT aggregation and join-column choice, delta/ratio
  math — lives in `dashboard_logic.py` with tests in
  `tests/test_dashboard_logic.py`. When you touch an inline decision in
  dashboard.py, extract it; don't grow it in place. The 2026-06 bug pair
  (#151 ".0" join keys, #156 format bump ordering) lived precisely in
  inline decision code where no test could see it. When extracting,
  prove behavior-identical against prod data (see PRs #185/#187 for the
  pattern: run old and new side by side, assert equality).

## Data sources currently wired
When auditing or adding data, the production sources are:

| Source | Client module | Cache tables (prefix) | Provenance |
|---|---|---|---|
| **Magnite DV+** | `client.py` (`MagniteClient`) | `magnite_*` | SSP delivery + deals |
| **Google Ad Manager** | `gam_client.py` (`GAMClient`) | `gam_*` (campaigns, pmp_deals, creatives, lica, …) | Direct delivery + PMP/PA/PD/PG |
| **Pubmatic** | `pubmatic_client.py` (`PubmaticClient`) | `pubmatic_*` | PMP deal report |
| **OpenSincera** | `opensincera_client.py` (`OpenSinceraClient`) | `opensincera_*` (ecosystem, publishers, adsystems, mapping_modules) | TTD's sell-side transparency / inventory metadata. Added 2026-05-22 (PR #44 + #46). Powers the OpenSincera dashboard tab with a Newsweek-vs-peers scorecard. |
| **DoubleVerify Attention** | `dv_attention_client.py` (`pull_dv_attention`) | `dv_attention` | DV Pinnacle "Authentic Attention" metrics per line item — 100-baseline indices (Attention / Engagement / Exposure / Intensity / Prominence / User Presence / Ad Interaction / View Presence) plus DV's view of viewability. Ingested via email: DV team mails the daily CSV to `newsweek@agentmail.to`, we poll the inbox via agentmail's v0 API and parse the attachment. Surfaces as the "Attention" column on Direct + PMP tables. Subject filter: `Unified Analytics Report: Attention Metrics`. Added 2026-05-24. Both DV parsers normalize `line_item_id` to integer strings at parse — blank open-exchange cells make pandas read the CSV column as float64, and an unstripped `astype(str)` yields `"…​.0"` keys that never join `gam_campaigns` (#151); the daily health check canaries this. |
| **DoubleVerify IVT** | `dv_ivt_client.py` (`pull_dv_ivt`) | `dv_ivt` | DV Pinnacle invalid-traffic classification rows (Valid Traffic / Fraud/SIVT / Fraud/GIVT) per line per day, with `Monitored Ads` impression counts. Same email pipeline as DV Attention; subject filter: `Unified Analytics Report: IVT`. The dashboard computes **impression-weighted IVT%** per MRC standard: `Σ Monitored Ads (Fraud rows) / Σ Monitored Ads (all rows)`. Surfaces as **separate "SIVT" and "GIVT" columns** on Direct + PMP tables (MRC distinction: SIVT = data center / bot fraud / hijacked devices / emulators / app + site fraud, hard to detect; GIVT = self-identifying bots / declared crawlers, standard detection). Color bands tuned to industry IVT thresholds: green <1%, amber 1-3%, red ≥3%. Added 2026-05-24. |
| **Improvado betting CPA** | `improvado_client.py` (`pull_improvado`) | `betting_conversions` | Spinfinite/Improvado daily CPA report for the betting/gambling Direct campaign (order 4068491190). Improvado's AI Agent mails a tab-separated text report (subject contains `Newsweek - Daily report`) covering ~14 days of clicks, registrations, FTPs (first-time purchases), and Net Cash, bucketed by `Sub ID 1` (creative size) and optionally `Sub ID 2` (`li<line_item_id>` once test LIs are live). Same agentmail inbox as DV — reports are typically **forwarded** by the AE, so the sender filter is dropped and provenance is verified by requiring the `Generated by Improvado AI Agent` footer in the body. Joins to GAM delivery via `sub_id_2`'s `li<id>` parsing → `gam_campaigns.line_item_id`. Powered the segment-level CPA optimization loop for the IO1109 flight. Added 2026-05-25. **RETIRED 2026-06** — campaign paused mid-flight; `betting_conversions` dropped from prod. Client kept as the pattern for the next CPA-sold flight. |

`refresh_cache.py main()` accepts `--mode={all,direct,opensincera}`. Default is `all` (full sweep). Each source has a corresponding `refresh_<source>` function callable individually for ad-hoc work. DV Attention is folded into the full sweep — no `--mode=dv_attention` flag because the agentmail poll is cheap (~3s + however long DV's CSV is to parse).

**Per-report retention (the no-duplicate invariant).** The three PMP daily
tables — `gam_pmp_deals`, `magnite_deal_daily`, `pubmatic_deals` — pull a
**14-day** window so the dashboard can grade **week-vs-week spend momentum**
(`dl.spend_momentum`, adaptive last-7-vs-prior-7; degrades to 3-vs-3 on a
shorter cache). Everything else still reads 7 days: the PMP summary windows
itself back via `dl.window_last_n_days(…, n=7)` so its Revenue/Impr/eCPM
totals don't move, and `revenue_daily_series_by_deal` already takes the last
7. The retention rule for any append-with-DELETE table (`refresh_one_report`,
`refresh_pubmatic`) is **`retention_days == pull_window + 1`** — the
`DELETE WHERE date >= cutoff` must clear *yesterday's oldest row* so the fresh
pull replaces the window cleanly; mismatch duplicates the non-deleted tail
(a 14-day pull on the old shared 8-day cutoff accumulated 33 days / 276 dup
rows in a Supabase sim). So `magnite_deal_daily` carries `window_days=14` +
`retention_days=15` while `magnite_site_daily`/`magnite_dsp_daily` keep their
`last_7` preset + default-8 retention, untouched. `gam_pmp_deals` is
`_safe_replace` (full TRUNCATE+append), so widening its window can't
duplicate. The two raw SSP tabs (Magnite/Pubmatic) are date-picker windowed
(default "Last 7 days"), so they just gain range. Verify any retention change
with a temp-table sim of N consecutive daily runs before it touches the sweep.

`pmp_last_bid_date` is a **cumulative** tracking table (not a 7-day rolling window). Upserted at the end of every full sweep by `refresh_pmp_last_bid_date()`. Schema: `(ssp, deal_key, last_bid_date, last_seen_date, first_seen_date, updated_at)`. `deal_key` is `deal_meta_id` (Pubmatic), `deal_id` (Magnite), or `programmatic_deal_name` (GAM). Powers the "Stale deals" expander on the PMP tab. **`last_seen_date`** (added 2026-06) is the last day the deal appeared in ANY source row (`MAX(date)`, bid or not) — distinct from `last_bid_date` (`MAX(date WHERE bids>0)`). Both move forward monotonically via `GREATEST` in the upsert. The expander shows deals stale by `dl.stale_deal_mask` (no bids 90+ days) **and** `dl.recently_seen_mask` (still seen within 90 days): a deal that stopped being reported entirely (paused/removed) drops off, while a deal still in the source but not winning bids stays (actionable). This is why **paused deals used to linger forever** — the table never prunes and the old logic only knew bid recency, so a paused deal went stale and never left; `last_seen_date` is the fix. The source tables only retain ~7–30 days, so a true 90-day "not seen" window can't be computed from them directly — it has to be *tracked* over time (hence the column). `recently_seen_mask` no-ops while the column is NA/absent (old cached frames), so behaviour is unchanged until the refresh populates it. (Migration: existing rows seeded `last_seen_date = COALESCE(last_bid_date, first_seen_date)`, then bumped from current source — done in `refresh_pmp_last_bid_date`'s startup and run once against prod 2026-06-14.) GAM PD/PG deals can be archived directly via `GAMClient.archive_proposal_line_item(pli_id)` (SOAP `ProposalLineItemService`). Pubmatic and Magnite require manual action in their SSP UIs (no publisher-side archive API).

For one-off DV backfills (manually downloaded Pinnacle CSV), use `scripts/seed_dv_attention.py /path/to/file.csv`.

For first-deploy seeding of `pmp_last_bid_date` with 90 days of history, use `scripts/seed_pmp_last_bid_date.py` (default `--days=90`, `--sources=pubmatic,magnite,gam`). Safe to re-run — upsert never regresses `last_bid_date`. Supports `--dry-run` to preview without writing.

## Outbound daily digests

| Digest | Script | Workflow | Recipients (var) | Subject |
|---|---|---|---|---|
| ~~Betting CPA (Spinfinite, IO1109)~~ **RETIRED** | `betting_daily_update.py` | `.github/workflows/betting_daily_digest.yml` | `BETTING_DIGEST_TO` (var) / `BETTING_DIGEST_CC` (var, optional) | `Newsweek Betting CPA digest — <yesterday>` |
| Data health check | `health_check.py` | `.github/workflows/health_check.yml` | `HEALTH_DIGEST_TO` (var, default roger.hirano@newsweek.com) | `Yield health — ✅ N/N pass (<today>)` / `❌ N of M FAILING (<today>)` |

The health check runs after the sweep and verifies prod data invariants: DV
`line_item_id` hygiene (the ".0" float-suffix canary from #151), DV↔GAM join
rate ≥90%, per-table freshness (same-day sources must have yesterday's date;
Pubmatic +1 day, DV may lag 3; OpenSincera's four tables by `_pulled_at`
within 26h), and that the latest `refresh.yml` run succeeded within 26h. **Auto-remediation:** when a *remediable* check fails
(stale table / failed sweep), the script re-dispatches `refresh.yml` itself,
waits for it, re-checks everything, and reports the final state — transient
upstream failures heal hands-free. Code-level failures (id format, join
rate) are reported as needing a human; a re-pull can't fix those. Disable
with `HEALTH_AUTO_REMEDIATE=0` or the workflow's `remediate` input.
**Retry ladder:** seconds-scale blips are retried inside the clients
(Magnite 429 ×10 / 5xx ×3, GAM SOAP ×3, Supabase pooler connect ×4 — the
six parallel sweep jobs stampede the pooler at 09:00 UTC and the initial
connect can time out, 2026-06-11); the 09:45 UTC check re-runs the sweep
once, immediately (the sweep itself fires 09:00 UTC / 05:00 ET); the
13:45 UTC follow-up check retries once more ~4h later. The first green
run of the day emails the ✅; later green runs are quiet — keyed on the
workflow's own run history, NOT the clock, because GitHub cron drifts
6-8h and an hour-based gate silenced entire green days (2026-06-11).
Failures and remediation outcomes always email. Max two
sweep re-runs/day — anything still failing after that needs a human, and
the ❌ email + red Actions run says so. The
subject carries the verdict, so a ✅ day needs no opening; set repo
var `HEALTH_DIGEST_ONLY_FAILURES=1` to silence every green email. It is
triggered by **launchd on Roger's Mac**
(`~/Library/LaunchAgents/com.newsweek.yield-health-check.plist`, same
host as the Confiant jobs) firing `gh workflow run health_check.yml` at
05:45 + 09:45 ET (ET-pinned like the sweep; `RunAtLoad` catches boots
after missed fires, and redundant fires are free thanks to the
run-history gate). GitHub-native cron drifts hours late and
auto-disables after 60 days of repo inactivity, so it is not the
trigger of record; cron-job.org (`workflow_dispatch` + PAT, like
`refresh.yml`) is the alternative if the Mac dependency becomes a
problem. One `schedule:` cron remains (18:00 UTC) as a **dead-man
fallback**: quiet when an earlier run already sent today's verdict, but
if the Mac is off all day or gh auth breaks it becomes the first run of
the day and the verdict still goes out — late, which is the tell. The
script exits non-zero on any failing check, so the Actions run goes red
and GitHub's failure email fires as a second signal.

**Betting CPA digest is retired.** The IO1109 Spinfinite campaign was
paused mid-flight and no longer runs (per Roger, 2026-06-10); the digest
went dormant ~2026-06-04 and `betting_conversions` was dropped from prod,
so dispatching the workflow now would crash on the missing table. The
script, workflow, and `docs/betting_cpa.md` are kept as the reference
pattern for the next CPA-sold flight (outbound send, sub_id join
contract, segment test design). The data health check intentionally does
not cover `betting_conversions`.

Digest scripts share the outbound `POST /v0/inboxes/<inbox_id>/messages/send` pattern from `apple-news/daily_report.py`, triggered externally by cron-job.org via `workflow_dispatch` (GitHub-native `schedule:` drifts hours late), and support a local dry-run — `python <script> --dry-run` (needs `DATABASE_URL` in env; the flag skips the send only).

## Dashboard ad-format taxonomy
**Seven canonical formats** (owner-defined by Roger, 2026-06-12; PRs
#189–#193): **Display, Video, Interstitial, Interscroller, FITO,
Centerstage, Apple News**. The source of truth is
`dashboard_logic.CANONICAL_FORMATS` + `derive_format()` /
`canonicalize_format()`, all pinned by table-driven tests in
`tests/test_dashboard_logic.py` that assert every format value observed
in prod into its bucket.

How a line item gets its format (`derive_format`, in order):
1. **Name keywords beat the API** — GAM's `INVENTORY_FORMAT_NAME` has no
   vocabulary for the house formats and flattens interstitials / FITO /
   Centerstage / Apple News / Interscroller into "Banner" (FITO video
   into "In-stream video"). Keywords match anywhere in the line-item
   name (precedence: fito → apple-news → centerstage →
   interscroller/uniscroller → interstitial), which also survives
   token-position drift (the AppleTv Cape Fear names carry their format
   word at position 11, not the convention's 10).
2. The API value, canonicalized — authoritative for the display/video
   families ("Banner"→Display, "In-stream video"→Video).
3. The position-10 name token, canonicalized.

Canonicalization facts: **Uniscroller folds into Interscroller** (same
product, two names). There are **no Native or Multi buckets** — both
fold into Display, as do branded-article promos, size-named placements
(Backfill-970x250…), and Homepage-Insight. Junk tokens from
non-convention names (initials, prices, geos, "cpm") resolve to **None**
— table-visible, absent from the Format filter, default benchmark
fallbacks. `format_aliases` in Settings re-routes any outcome and wins
over the rules; new legitimate formats surface in the Settings
unmapped-formats panel until given a rule or alias.

**"Video Preroll >30s" is a benchmark band, NOT a format** ("it's just
one video"). The Format filter shows plain Video; a separate
`_bench_format` column carries the band (duration bump >30s + manual
`long_preroll_lines` rules) and only threshold lookups read it — so
long-form video grades against its own VCR line (35% red, per the
benchmarks settings; the #156 fix) while filtering as Video. Its
thresholds live under the "Video Preroll >30s" row of the Benchmarks
editor, which is the band's only user-facing surface.

**Direct row display name = `<Advertiser> — <Campaign>`**
(`dl.line_item_display_name`, advertiser = name token 7, campaign = token
8). The **campaign** carries the placement/product
(Newsmakers-Centerstage, Qx65-Homepage-Takeover, Apple-News,
Custom-Audience-Pre-roll, MANV-Sponsorship, …), so it's what tells sibling
LIs apart — the old name used **token 10 (format)** and collapsed a whole
advertiser's book into one string (34 real Infiniti LIs → one
"Infiniti - Display"). Format is intentionally **dropped from the name**:
it's redundant with the canonical chip and the raw token-10 is often wrong
(Apple-News and Centerstage lines both carry "Display" at token 10), so the
chip (`derive_format`) is the single source of truth for format and the name
is identity-only. Cleaning: strip the leading `#N` badge, drop the advertiser
prefix the campaign token repeats (`Infiniti-Newsmakers-…` → `Newsmakers …`),
dashes→spaces, and **preserve a trailing `(Article)`/`(copy N)` marker** — on
the real Infiniti set that marker is the only thing separating a
same-campaign/same-format pair (34 LIs → 31 distinct names; the 3 remaining
repeats are one campaign run in two formats, which the chip separates). This
is also the Direct table's A–Z **sort key**, so it must equal what the row
renders; changing it regroups the table by advertiser→campaign. Pinned by
`test_line_item_display_name` + `test_line_item_display_name_real_prod_names`.
The leading **`#N` badge** numbers LIs **#1, #2, … per GAM order, assigned in
the displayed (A–Z) order** — so every line of a multi-line order is badged and
they read low→high down the order's block (a single-line order shows none). The
table sorts A–Z by the display name (`line_item_id` tiebreak), and the per-order
`cumcount` runs **after** that sort, so it follows campaign-alphabetical order,
**not** `line_item_id` — which is what kills the old scatter (per-order-by-id
put `#6` above `#3/#4/#5`). An order is one advertiser, so its lines sit
together in the A–Z view and the badges are contiguous. (History: 2026-06-15
first tried per-displayed-campaign-group, but that dropped badges from unique
campaigns — most Infiniti/Jeep lines went bare — so reverted to per-order on
Roger's call, just numbered in display order instead of by id.)
**Two internal test/QA orders are hidden from the Direct view** —
`_EXCLUDED_ORDER_IDS` (`3648897741` = GMC "Terrain Diverse Owned TEST PAGE" /
CITIQ3, and `4082002976` = "Newsweek_Test-2", ~416 LIs combined). `gam_df` is
filtered on `order_id` right after load, so nothing from them reaches the
table, the KPI rollups, or the DV joins. Add an order id here to hide it.

**PMP deal display name = `<Advertiser> — <Campaign>` + agency subline**
(`dl.pmp_deal_display_name` → `(primary, sub)`). The Newsweek deal-name
convention (`Newsweek_<PG|PD|PA|PMP>_<vertical>_<exchange>_<dsp>_<holding>_
<agency>_<advertiser>_<campaign>_<geo>_<format>_<floor>_<team>_<ae>`) puts the
advertiser at **token 7** and campaign at **token 8** — the *same positions as
the Direct convention* — so deals read as `Advertiser — Campaign` with
**agency · holding** (tokens 6 · 5, the buyer, not shown in any column) as the
secondary line. The old `_parse_pmp_name` surfaced `vertical_exchange_dsp`
(e.g. "Automotive_Adx_DV360") as the primary and buried the advertiser,
collapsing distinct deals (two MD-Anderson intent tiers both read
"Health_Magnite_AdTheorent"). DSP/SSP/Format/eCPM/Deal Type/Seller are already
columns, so the name is identity-only. **SSP-native / non-convention names**
(Pubmatic `3PS_Pubmatic_DE_Display_High CTR`, DSP-minted `Google_US_Always-On_…`)
have no token-7 structure, so they're returned **whole, lightly cleaned**
(underscores→spaces) — the buyer's string *is* the identity. `''`/`NA`/`N/A` →
`("—", "")`. Used by both the desktop name cell and the mobile card primary
(and the floor-breach banner's `[0]`). Pinned by `test_pmp_deal_display_name`.

## Dashboard design system (Newsweek "Paper", 2026-06)
The dashboard is skinned to the Newsweek design system: **light warm-paper
canvas** (`--surface-0 #fefcf6`, ink text `#1f1e19`), Benton Modern
Display serif on H1/section titles/KPI figures, Franklin Gothic for UI,
tracked-uppercase eyebrows, tabular numerals everywhere. Source spec:
`docs/design_handoff/` (Claude Design handoff: token CSS + before/after
audit doc). A dark warm-ink variant shipped briefly on 2026-06-12 (PRs
#199/#200) and was superseded by this light version the same day (#201);
its ramp survives, commented, at the bottom of the handoff CSS.
Two places define tokens and **must stay in sync**:
1. The `:root` token tier at the top of dashboard.py's style block —
   every component rule reads only tokens.
2. `.streamlit/config.toml` `[theme]` — same values for Streamlit
   natives (widgets, canvas dataframes, fonts via `[[theme.fontFaces]]`,
   built-in/Altair chart palette via `chartCategoricalColors`).
Colors emitted as **literals** in dashboard.py (pandas-Styler cell
styles, Vega/Altair series, settings defaults) mirror the tokens with a
`--token-name` comment at each site — the data-grid canvas and Vega
can't resolve CSS vars.

Rules that survive any future restyle:
- **Two reds, never mixed.** `--brand-red #e91d0c` is chrome only: the
  eyebrow tick and the active-tab underline. `--state-critical #c41608`
  owns data severity. Acceptance rule: *if a red pixel is not the mark,
  a tab, or a breach, it's a bug.* (`primaryColor` is ink for the same
  reason — Streamlit paints buttons/focus/checkboxes with it.)
- **Severity is tint, not shout**: banded cells/pills = `--state-*-surface`
  background + saturated `--state-*` text; in-range values stay plain
  colored text. Thresholds/banding logic untouched — lives in
  `dashboard_logic.py`. **Exception — the Direct pace cell** (`_pace_html`,
  2026-06-14): on owner request it boxes *every* state for cell
  consistency, but on-pace uses a **quiet** green pill (`.pill-green` =
  `--state-positive-surface-quiet` + `--state-positive-muted`), one tier
  below the loud amber/red exception pills, so healthy still recedes and
  the exceptions keep the page (the green-overwhelm rule survives). The
  other in-range cells (viewability, CTR/VCR) keep plain `.txt-green`.
  The pace cell's **"new line item"** sub-label is **existence-based**
  (`dl.is_new_line_item`, 2026-06-14): it shows when a line didn't exist
  the prior day — its first delivery is the latest day
  (`lifetime_impressions_delivered == impressions_1d`, latest-day > 0) —
  not from a large pace swing. So a brand-new line (no real pace trend to
  compare) reads "new line item", while an established line with a genuine
  >100pp jump now shows the actual Δ (the pace delta passes
  `new_line_threshold=None`). Rate metrics never triggered the old swing
  flag anyway (bounded ≤100).
- **Green is asymmetric** (the green-overwhelm rule, 2026-05-25; muted
  tier added 2026-06-12 after the first themed deploy glowed green):
  high-frequency "fine/improving" signals — per-cell ▲ deltas, in-range
  pace/eCPM text, progress bars, all-clear banners, on-track chart
  lines, gaining momentum rows — use `--state-positive-muted`
  (`#6f8f56` on paper); the all-clear banner tint runs quieter than
  red/amber. Saturated `--state-positive #3c6b14` is reserved for
  green-as-a-signal: status chips/pills, enabled badges. Amber/red are
  always loud — healthy recedes, exceptions own the page.
- **Sparklines are neutral** (`--text-secondary`) — trend shape only;
  severity belongs to bands/banners. The drawer 7-day delivery chart is
  the one state-colored line (it *is* a pace-health signal) and carries a
  faint same-color area wash (`fill-opacity .10`) + a `--border` baseline.
  Chart series read the `--viz-*` palette; the OpenSincera peer charts render
  Newsweek = ink vs peers = warm gray — never brand red on a series.
- **Inline-SVG geometry — width-fill vs uniform (the 2026-06-13 "distorted
  graph" bug, fixed in three parts):** the rule is **the wider the box a chart
  occupies, the less it may stretch.** Pixel-fixed-height SVGs that fill width
  under `layout="wide"` get crushed flat — so anything wider than a KPI tile
  scales uniformly.
  - `_sparkline_svg` **default** (`uniform=False`, the KPI tiles **and** the
    Direct mobile graph-card delivery sparkline) are **compact fixed-height
    sparklines** that *do* stretch to fill width (`preserveAspectRatio="none"`).
    The tile is only ~130px, so the stretch is mild; every stroke is still
    pinned with `vector-effect="non-scaling-stroke"` and end-dots are drawn as a
    **zero-length round-capped `<path>`** (`d="M{x} {y}h0"`,
    `stroke-linecap="round"`), never a `<circle>` — a circle smears into an
    ellipse at the rendered width. The stretch regime keeps the line **flush**
    (`XPAD=0`, end dot at `x=W`), so the dot's 4px round cap pokes ~2px past the
    viewBox edge; the svg carries **`style="overflow:visible"`** (stretch regime
    only) so that cap renders into the adjacent margin instead of the viewport
    clipping it to a half-dot (the 2026-06-13 "cut-off dot" on the mobile card).
    Uniform mode insets with `XPAD` instead, so it needs no overflow.
  - `_sparkline_svg(uniform=True)` (the **drawer small multiples** —
    Viewability + CTR/VCR + Attention + SIVT + GIVT, the last three added
    2026-06-13; Attention targets 100, SIVT/GIVT target 1%, and their per-LI
    daily series come from the precomputed `dl.attention_daily_series_by_li` /
    `dl.ivt_daily_series_by_li` dicts — one groupby pass, not a per-row scan of
    the 290k-row IVT table; a panel is skipped when the line has <2 days of DV
    coverage) scales **uniformly**: wide viewBox (`300×34`), *no*
    `preserveAspectRatio="none"`, CSS `width:100%; height:auto`. Their `.nw-sm-grid`
    panels run ~370px on the wide layout, so the old stretch crushed the trend
    flat exactly like the delivery chart did (the "viewability/CTR still
    distorted" follow-up). The wide viewBox keeps the rendered height compact
    while scaling proportionally; an `XPAD` x-inset keeps the end dot off the
    panel edge. **Don't pass these through the default stretch path.**
  - `_drawer_delivery_chart` is a **real chart**, so it scales **uniformly**
    (plain `viewBox` `600×112` + CSS `width:100%; height:auto`, *no*
    `preserveAspectRatio="none"`) — geometry is never warped. Its panel
    (`.nw-drawer-chart`, and the sibling `.nw-sm-grid`) is capped at
    `max-width:760px` so on the wide layout it stays a proportioned card
    instead of a stretched-flat band; the date row sits inside the panel
    and caps with it, staying aligned under the 7 points. Don't reintroduce
    `preserveAspectRatio="none"` here.
  - `_pmp_drawer_trend_chart` (the PMP deal drawer's neutral 7-day trend
    charts — **revenue · total requests · bid responses**; revenue-only until
    the bid-funnel pair was added 2026-06-14) is the delivery chart's twin —
    same `600×112` uniform scaling + area-wash + baseline + end-dot — but
    **NEUTRAL** (`--text-secondary`): a trend is shape, not a pace-health
    signal, so the eCPM-vs-floor banding keeps severity and the line stays
    neutral (the delivery chart is the *only* state-colored line). `money=False`
    drops the `$` for the count charts (K/M formatting), and **each chart skips
    when its metric sums to ≤0** — so Pubmatic (its `total_requests` is
    unpopulated upstream) shows revenue + bid responses, and any deal without
    funnel rows shows revenue only. **All three SSPs report the bid funnel** —
    the earlier "GAM has no funnel" assumption was wrong: GAM's per-deal funnel
    lives in a *separate* table, `gam_deal_bid_daily` (`deals_bid_requests` =
    ad requests, `deals_bids` = bid responses), keyed by `programmatic_deal_name`
    — the same Deal key, so it merges with the GAM revenue rows on
    `(ssp, deal, date)` in the per-column groupby-sum (45 delivering GAM deals
    carry it). The per-deal series come from
    `dl.daily_series_by_deal(_pmp_daily, <col>)` (revenue via the
    `revenue_daily_series_by_deal` wrapper, which the test still pins) —
    total_requests from GAM `deals_bid_requests` / Magnite `bid_requests` /
    Pubmatic `total_requests`, bid_responses from GAM `deals_bids` / Magnite
    `bid_responses` / Pubmatic `non_zero_bid_responses`. `_pmp_daily` rebuilds a
    daily frame from `gam_pmp_deals` (revenue) + `gam_deal_bid_daily` (funnel) /
    `magnite_deal_daily` / `pubmatic_deals` (the daily rows the PMP summary
    aggregates away) keyed by **(SSP, Deal)** — match each source's row key
    exactly (GAM `programmatic_deal_name`, Magnite `deal`, Pubmatic `deal_label`
    = deal→publisher_deal_id→deal_meta_id) or the lookup misses. The window is a contiguous last-7-days ending at the latest date
    present (PMP lags ~2 days), 0-filled. The chart + the card sparkline
    (`_pmp_spark_svg`) are **self-contained in the PMP scope** — the Direct
    `_sparkline_svg` lives behind `if gam_df.empty: … else:` and is *not*
    reachable from the PMP block, which always runs. Pinned by
    `test_revenue_daily_series_by_deal`.
  - `_pmp_spark_svg` (the PMP card revenue sparkline) **scales UNIFORMLY**
    (wide `300×34` viewBox, *no* `preserveAspectRatio="none"`, `XPAD` inset,
    CSS `height:auto`) — **NOT** the Direct card's stretch regime. The PMP
    card's spark box is ~9:1, far from a `56×20` viewBox, and under that
    anisotropic `preserveAspectRatio="none"` stretch iOS Safari distorts the
    non-scaling round end-cap into a smeared horizontal blob and thickens the
    line unevenly (the 2026-06-14 "graphs look off" bug). The Direct card
    survives the stretch only because its box is near-square (~3:1, close to
    the viewBox). Rule: a stretch-regime sparkline is only safe when its
    rendered box stays near the viewBox aspect; for a wide-and-short box, go
    uniform.
- **Categorical chips read from `--viz-1…6`** (deal-type pills, seller
  hash colors), never the state scale.
- Fonts: licensed binaries go in `static/fonts/` (drop-in, gitignored;
  see its README; served via `enableStaticServing`); fallbacks Georgia /
  Helvetica apply while it's empty.
- **Responsive: desktop-first + a mobile override block** at the bottom of
  the style block (behind `@media` queries, source-ordered last so they
  win). Desktop layout is untouched. ≤1024px: the 9-up KPI strip becomes
  a fluid `auto-fit minmax(96px,1fr)` grid. That auto-fit packed 4 tiles
  across on a phone and crushed them (labels wrapping mid-word), so ≤640px
  the strip is **pinned** explicitly: the main 9-tile `.nw-kpi-row` to
  3-up (clean 3×3), the PMP 4-tile `.nw-kpi-row.nw-kpi-row--pmp` to 2-up
  (2×2). That PMP modifier is a **two-class selector** (not an inline
  style) precisely so it outranks the ≤1024 single-class auto-fit rule and
  holds 4-up on desktop/tablet while the ≤640 rule can still take it to
  2-up.
  ≤640px: banners stack 1-up, the tab row stays horizontally swipeable
  (`overflow-x:auto`). Both the **Direct** and **PMP** 12-column tables
  **collapse to graph cards** (see the next bullet). Drawer meta-grid 4→2.
  Most tab filters reflow for free (Streamlit stacks `st.columns`). When
  adding a fixed multi-column grid, add its mobile rule here too.
- **Stale deals = a read-only row in the PMP signals accordion** (no bid
  responses for 90+ days, still seen in the source — `dl.stale_deal_mask`
  **AND** `dl.recently_seen_mask` over `pmp_last_bid_date`, so paused/removed
  deals already drop off). Each sub-row is the `Advertiser — Campaign` name
  (`dl.pmp_deal_display_name`) + a meta line (SSP · last bid · **days-idle**
  colored by `dl.idle_band`, amber 90+ / red 180+, via `.nd-idle`), sorted
  most-idle first. Folded up from a standalone `st.expander` 2026-06-14;
  the **Archive action was removed** ("no longer needed", per Roger) along
  with its creds-gating helpers (`_gam_creds_ready` / `_gh_dispatch_ready` /
  `_dispatch_archive_workflow`), the secret-capability diagnostic, and the
  `.nw-stale-*` CSS — so the row is plain static HTML and lives inside the
  accordion (which can't host Streamlit buttons). The backend archive path
  is untouched if ever wanted again: `GAMClient.archive_proposal_line_item`
  (SOAP), `scripts/archive_pli.py`, `.github/workflows/archive_pli.yml`.
- **Direct + PMP tables → "graph card" rows on mobile (Solution 3).** Each
  row is a `<details>` whose summary is the 12-column grid on desktop; the
  builder also emits a hidden mobile card. ≤640px a marker class on the
  table wrap swaps them — `summary`/`.nw-*-row` drops `display:grid`,
  `> *:not(.nw-*-m)` is hidden, and the card shows — so the row reads as a
  compact card with no horizontal scroll, and tapping it still opens the
  same drawer. Desktop is untouched.
  - **Direct** (`.nw-tbl-direct` → `.nw-row-m`): name + pace bar + 7-day
    delivery sparkline + revenue/pace. Pace bar reuses the row's pace
    banding (`_pacing_critical`/`_warn_low`/`_warn_high`); sparkline is
    `_sparkline_svg(_row_daily_imp_series(row), klass="")` (compact stretch
    regime). Both visuals carry a tiny muted uppercase eyebrow
    (`.m-pbar-l` "pace" / `.m-spark-l` "delivery 7d") so the bare bar
    isn't ambiguous — the bar shipped unlabeled and read as a mystery on
    the live card (2026-06-13).
  - **PMP** (`.nw-tbl-pmp` → `.nw-pmp-m`): deal name (left) + an
    **eCPM-vs-floor bar** + a **7-day revenue sparkline**; the right column is
    the **deal-type pill** (`.m-dt`, top, fixed spot) over revenue / eCPM /
    impressions. The bar scales eCPM against `2 × floor` so the floor sits
    at the **50% tick**, and bands like `_ecpm_cell` (under floor amber,
    ≥2× floor green, otherwise neutral). The sparkline (`_pmp_spark_svg`,
    "revenue 7d" eyebrow) sits under the bar — both are kept (the bar is the
    yield-health signal, the sparkline is the trend). The type pill is pinned
    **top-right** rather than inline after the name: deal names vary in length
    (and wrap), which scattered the pill across the column (2026-06-14).
- **Both big tables paginate at 25 rows/page** (`_DIRECT_PAGE_SIZE` / PMP
  `_PAGE_SIZE`). The Direct (`direct_page`) and PMP (`pmp_page`) tables each
  render only the current page's slice into the custom HTML grid, with the same
  pager **above and below** (hidden when there's a single page). Slicing is
  positional (`.iloc`) so it **preserves the index labels** — the Direct per-row
  `_vw_rate`/`_ctr_rate` lookups (`view_gam.index.get_loc(row.name)`) still
  resolve on a sliced page. **Page resets to 0 on any filter change** (a
  filter-signature guard, `_direct_filter_sig` / `_pmp_filter_sig`) so a narrower
  filter can't strand you on an out-of-range page; the index is also clamped to
  `[0, N-1]` each run as a backstop. The Direct table was **un-paginated until
  2026-06-14** — it built every filtered LI (thousands in cache) into one DOM per
  rerun; the PMP table paginated earlier.
  - **The pager is a compact one-row bar** (`_compact_pager`, 2026-06-14) —
    `‹` · centered **Page X of N** (+ a muted "N of M shown" subline) · `›`. It
    replaced a `st.columns([1,4,1])` + full-width `st.button` layout that
    **stacked into three full-width blocks on mobile** (Roger flagged it as
    bulky). The shared helper wraps the two arrow buttons + an HTML caption in a
    keyed **`st.container(horizontal=True)`** (the same inline-on-mobile trick
    the filter bars use); CSS hooks `.st-key-nwpgrwrap_*` — `space-between` pins
    the arrows to the edges of a `max-width:430px` centered bar (so desktop
    doesn't sprawl), arrows are 46px squares, disabled at `opacity:.4`. The
    button keys are `nwpgrbtn_<name>_{prev,next}`, container key
    `nwpgrwrap_<name>` (`name` ∈ direct_top/bot, pmp_top/bot) — kept on distinct
    prefixes so the container CSS selector doesn't also catch the button
    wrappers. Page-state vars/callbacks (`_direct_cur_page`, `_pmp_go_next`, …)
    are unchanged — only the rendering moved into the helper.
- **Campaigns filters are a popover + active chips, not a dropdown row.**
  The six Campaigns filters (Seller / Advertiser / Format / Status /
  Team / Account Manager) live inside one `st.popover` whose trigger
  reads "Filters · N"; whatever is applied renders as removable chips
  beside the trigger, both inside a keyed horizontal container
  (`st.container(horizontal=True, key="nw_filter_bar")` →
  `.st-key-nw_filter_bar`, which the pill CSS hooks). Chips clear via
  `on_click` callbacks that reset the widget's `st.session_state` key;
  the six widget **keys are unchanged**, so the filtering logic below is
  untouched. The active list/count is read from `st.session_state` at the
  top of the run (Status seeds its default through the `_status_ver`
  guard) so it reflects the latest selections. This replaced a
  `st.columns(6)` row that stacked into six full-width dropdowns on
  mobile and buried the data below the fold. The **PMP deals** section
  lower on the same page got the identical treatment — a second keyed
  container (`nw_pmp_filter_bar`) for its six multiselects (Deal Type / SSP /
  DSP / Format / Deal Source / Team) plus one **option toggle** in the same
  popover: **Show deals under $100/day** (`pmp_show_low_rev`, default off —
  was a standalone checkbox above the table, moved into the popover; filters
  only the table view `_pmp_display`; when it hides deals the table subtitle
  spells out the gap — `N of M shown · K under $100/day hidden` — so the
  difference doesn't read as missing data). The **Deal Type** multiselect
  **defaults to `Private Auction` / `Preferred Deal` / `Private Marketplace`**
  (Programmatic Guaranteed excluded on load) — seeded once into
  `st.session_state["campaigns_pmp_deal_type_filter"]` (intersected with the
  configured types) so the multiselect picks it up with no `default=` arg, and
  it shows/clears through the existing Deal-type chip (clearing it restores
  All, PG included). This replaced a brief standalone "Exclude PG" checkbox
  (reverted — the Deal Type filter already covers it). The PMP-signals
  accordion is `<details … open>` (default-open even on mobile; the separate
  Needs-attention card keeps its collapsed-on-mobile default since `open` is
  per-element). The other tabs (By site / size, By DSP, Pubmatic, Magnite)
  still use plain `st.columns` filter rows.
- **Campaigns alerts are a "Needs attention" accordion, not three stacked
  banners.** The pacing/viewability exceptions render as one `.nw-na`
  card with a row per category. A category with offenders is a native
  HTML `<details>`/`<summary>` row (browser-native expand/collapse, **no
  Streamlit rerun** — the markdown sanitizer passes `<details>`), and
  expanding it reveals that alert's line items inline (worst-first, a
  **two-tier label** — advertiser bold over the muted campaign
  (`dl.line_item_display_name` split on ` — `), metric as colored text on
  the right, **no bar** — so sibling LIs (3 Pateks differing only in the
  campaign tail Male/Female/Added-value) are identifiable and match the
  table; `_short_advertiser` alone collapsed them). Clear categories render as a static `sev-ok`
  row; rows default collapsed. Counts keep the existing
  `head(4)`/`head(6)` display cap (so the count matches the revealed
  rows). Bars/dots/counts inherit the row's `sev-*` and obey the
  two-reds rule (`--state-critical` / `--state-warning` only). The PMP
  tab still uses the simpler `.nw-banner` strip. The builder
  (`_na_row` / `_na_subrows`) is rendering, not decision logic — the
  offender sets (`_under_rows` / `_over_rows` / `_vw_anom_rows`) are still
  computed upstream from the configured benchmarks.
  **The whole card is itself a `<details>`** (when there are flags): on
  mobile it collapses to one compact header line — it was dominating the
  first screen above the KPIs (2026-06-14) — and the category accordion is
  one tap away. Desktop/tablet (`@media min-width:641px`) force
  `.nw-na-body { display:block !important }` + hide the chevron, so the
  always-expanded layout there is unchanged (CSS can't toggle the
  `open` attribute responsively, so force the body open instead). The
  all-clear state stays a plain `<div>` (three static ✓ rows, nothing to
  collapse).
- **PMP signals accordion** (under the PMP KPI strip, 2026-06-14). One
  `.nw-na` card (reuses the Needs-attention CSS, so it collapses to a
  one-line header on mobile and is forced-open on desktop) titled "PMP
  signals", **default-open** (`<details … open>` — per-element, so the
  separate alerts card stays collapsed). One inline-expanding row per
  signal.
  **Each deal inside a signal is tap-to-expand to the same drawer the main PMP
  table row opens** (`_render_pmp_signals` / `_sig_deal_wrap`, 2026-06-14):
  delivering deals (matched in `_combined_prefilter` by `Deal` name — momentum
  deal keys equal `combined_pmp["Deal"]` per SSP, so they hit) get the full
  `_pmp_drawer_html` (yield banner · 7-day revenue chart · bid metrics ·
  metadata grid); no-delivery / long-stale deals (no delivery data) expand to a
  **setup grid** instead (status/floor/dates, or SSP/last-bid/first-seen).
  **Render-ordering gotcha:** the card draws into an `st.empty()` slot placed
  under the KPI strip, but is *built* by `_render_pmp_signals()` called
  **after** `_pmp_drawer_html` + the revenue series are defined further down —
  the slot keeps the visual position while letting the deal rows reuse the
  table's drawer (defined later in the script). `_sp_rows_for` grew an optional
  `wrap` callback so momentum rows wrap the same way. The per-signal rows:
  - **Spend momentum** (neutral `sev-info`): the combined-PMP movers list.
    Decision logic is `dl.spend_momentum(df, name_col, rev_col)` (tested) —
    GAM + Magnite + Pubmatic PD+PA normalized to `(deal, _date, _rev)`,
    concatenated into **one** list (no per-SSP buckets, **PMP-only**, no
    Direct), split into an **adaptive** recent-vs-prior window
    (`w = min(7, D//2)` distinct dates → 7-vs-7 once the pulls carry 14
    days, behaviour-identical 3-vs-3 on a 7-day cache), filters deals to
    ≥$0.50 in a window then `|Δ| > $100`, sorts by recent revenue. Rows
    (`_sp_rows_for`, the `.sp-*` classes) are a two-tier advertiser/campaign
    label (`dl.pmp_deal_display_name`) + `prior → recent` flow + colored Δ
    (gaining `--state-positive-muted` / losing `--state-critical`). Header
    count = gaining + losing.
  - **No delivery** (`sev-red`): seller-owned PA inventory set up but not
    winning impressions, from `gam_pa_metadata`. **Excludes** deals that
    are actually delivering (present in `gam_pmp_deals` with impressions),
    **canceled** (dead by intent), and **open-auction** backstop (AE token
    `OpenAuction` — Google demand facilitation, not AE-managed). Grouped by
    **seller** — the AE parsed from the deal name (`Team-(USA|INTL)_<AE>`)
    resolved through **settings.json `ae_names`** (which carries the case
    variants, so `ILee`/`Ilee` both → "Ivy Lee"), busiest seller first.
    **Each seller is a collapsible row** (`.nd-sg` `<details>`, 2026-06-14):
    an initials avatar (`.nd-av`) + name + `count · worst-Nd` summary, deals
    nested inside, collapsed by default — so the seller overview is scannable
    and a 18-deal seller doesn't bury the rest (the prior flat list with a
    faint `.nd-ghead` header made "which deals belong to which seller" hard
    to read). Each card: readable `dl.pmp_deal_display_name`, a **PA/PD deal-type
    pill** (`_dt_pill`) pinned top-right (`.nd-top` flex), an Active/Pending
    status label (pending amber), and **days-inactive** =
    `dl.idle_days(last_bid_date, create_time[:10], today)` — last bid from
    `pmp_last_bid_date` when tracked (true inactivity), else days since
    `gam_pa_metadata.create_time` (set up but never bid) — colored by
    `dl.idle_band` (amber 90+, red 180+); most-inactive first within each
    seller. Header detail surfaces the actionable **active** count.
  - **Stale deals** (`sev-amber`): the read-only stale-deals row (full
    detail in the "Stale deals" convention above) — no bid responses 90+
    days, still seen. Same `.sp-row` layout: `Advertiser — Campaign` + a
    meta line (SSP · last bid · `.nd-idle` days-idle). Folded in here
    2026-06-14; the archive action was removed.

## Streamlit Cloud deploy
**Production deploys from `main`** (since ~2026-05-22). Previously was pinned to `mac-studio`, but that branch is no longer the deploy target. Push to main → Cloud auto-redeploys within ~60s. Don't merge main → mac-studio out of habit unless someone has explicitly re-pointed Cloud back at it.

**`st.cache_data` survives code-only deploys.** Table loads (`load()`,
`_load_li_max_duration()`) cache for `_CACHE_TTL_SECONDS` (1h — was 6h
until 2026-06-12; the 6h guarded the Free plan's 5 GB egress cap and the
Nano disk-IO budget, neither of which binds on Pro + Micro compute),
keyed on function source — a push that doesn't change those functions
hot-reloads the script but keeps the old cached frames. So after fixing
data out-of-band (direct SQL against prod), the dashboard keeps
rendering stale frames until TTL expiry; clear via the app menu
(⋮ → Clear cache) or save Settings (which calls
`st.cache_data.clear()`). This bit us on 2026-06-10 after the DV
`line_item_id` backfill.

**`load()` column-projects the two big DV tables** (`_COL_PROJECT`, 2026-06-14).
`dv_attention` and `dv_ivt` carry many columns no view reads — 8 sibling
attention indices + 2 viewability-rate columns on Attention; the precomputed
`fraud_sivt_rate`/`givt_rate`/`ivt_rate` (the dashboard recomputes
impression-weighted from `monitored_ads`) + advertiser/eligible_impressions/
total_calls on IVT. `load()` selects only the consumed set (Attention:
`line_item_id, line_item_name, order_name, attention_index, date`; IVT: those
4 ids/date + `traffic_validity, monitored_ads`), cutting the cold-load wire
bytes **~56–60%** on the tables that dominate the Campaigns view's first paint
(measured 6.3→2.5 MB + 11→5.2 MB). **Gotcha:** if you add a consumer that
reads another DV column, add it to `_COL_PROJECT` or it won't be loaded — the
union must cover `_dv_attention_aggregates` / `_dv_ivt_aggregates` **and** the
publisher-wide drawer recompute. A projected SELECT that errors (column
renamed/dropped upstream) falls back to `SELECT *`, so the projection is a
pure optimization, never a hard dependency — but a silent always-fallback
means it stopped saving anything, so keep the names in sync.

**The campaigns view no longer loads the DV tables raw — it reads server-side
pre-aggregations** (`_load_dv_attention_agg` / `_load_dv_ivt_agg`, 2026-06-15).
The dashboard only needs per-(LI, date) / per-(order, date) / per-date Attention
means and per-(LI, date, validity) IVT `monitored_ads` sums, so those `GROUP BY`s
run **in Postgres** (like `_load_li_max_duration`) and the dashboard fetches the
reduced result (~42% fewer rows: dv_attention 24k→14k, dv_ivt 44k→25k, plus tiny
399-row / 7-row attention rollups) instead of the raw rows — and the ~7.7 MB raw
frames are no longer held. The grain is chosen so it *is* each `dl` aggregator's
first-level reduction, so feeding the pre-agg to the **unchanged**
`attention_current_and_prior` / `ivt_share_with_prior` / `*_daily_series_by_li`
is behaviour-identical to the raw rows (proven on prod: a real-order test through
the `dl` functions, 0 diffs, + 0/107 order-dates diverge). Two correctness rules:
**(1) Attention means don't compose**, so the per-order path (PMP column + KPI)
gets its **own** `GROUP BY order_name, date` query — *not* derived from the
per-LI grain, which would be a mean-of-means (exact only while creative counts
stay uniform, as they are today: 0/107). IVT `monitored_ads` **sums** compose, so
one `(LI, order, date, validity)` SUM frame serves every IVT path (per-LI,
per-order, sparkline, publisher KPI). **(2) each query's `WHERE` mirrors the
matching `dl` dropna** (e.g. `attention_index IS NOT NULL AND line_item_id IS NOT
NULL` for the per-LI grain). The `_COL_PROJECT` note above now only bites if a
raw DV `load()` is ever reintroduced — the main campaigns path doesn't call it.

## Subsystems with their own docs
- `docs/confiant_blocklist.md` — Confiant -> GAM Protection brand-safety
  pipeline. Three jobs that all read/write the same `state.sqlite`:
    1. **Daily blocklist push** (`confiant_blocklist.py`, launchd 04:00 ET) —
       pulls Confiant API, pushes per-creative Security-flagged Google
       domains to GAM Protection 28044902 ("Everything") via Playwright.
       Post-run summary email goes to `revops@newsweek.com` via agentmail.
    2. **Weekly RevOps digest** (`confiant_blocklist_weekly_report.py`,
       launchd Mon 09:00 ET) — rolls up the past 7 days of pushes,
       branded layout (KPI tiles, per-day bar chart, issue-type cards),
       emails RevOps. Layout matches the publisher brand-safety style.
    3. **HRAP seed + SSP forward**
       (`confiant_blocklist_seed_hraps.py` + `confiant_hrap_forward.py`,
       run manually when Confiant ships a periodic notice). HRAP list
       persisted at `data/confiant_hraps.json`; seeder pushes new entries
       to the same Protection in batches of 30; forwarder creates one
       Outlook draft per SSP partner via Microsoft Graph.
  All four scripts auto-load `~/code/yield-dashboard/.env` via the same
  `_load_dotenv()` helper. **Plist EnvironmentVariables dict must NOT
  redeclare keys as empty `<string></string>`** — `os.environ.setdefault`
  treats them as "already set" and the script silently skips. The first
  ~10 daily blocklist runs lost their summary email to exactly that bug;
  see `docs/confiant_blocklist.md` for the full debrief.
- `docs/confiant_outreach.md` (lives next to it) — weekly per-SSP
  Confiant outreach emails. Microsoft Graph drafts via
  `confiant_outreach_drafts.py`. Subject `<SSP>//<Publisher> — N flagged
  creatives on <publisher>.com (past 7 days)`. CC always
  `revops@newsweek.com` (settings.json). Per-SSP enhancements:
  `ssp_publisher_ids` (surfaces our pub-id in the body); contacts accept
  RFC 5322 display names (`"Tristen Fabricant <tfabricant@zetaglobal.com>"`).
- `docs/article_sponsor_logo.md` — "Presented by <logo>" strip at the right
  of the article breadcrumb row, served from GAM through the out-of-page
  unit `oop1` (first paint; engineering ships the client-rendered container
  — replaced `oop2` 2026-06-11). Out-of-page CustomCreative (SafeFrame OFF
  — required for the parent-DOM injection) self-scopes to article pages,
  self-heals via a parent-document watcher, fires once-guarded agency
  pixels and an MRC viewability beacon, and keeps GAM Active View honest
  by gluing the carrier slot onto the strip (carrier-reposition). Live flight: Infiniti Newsmakers
  LI 7336465381. Setup script: `scripts/setup_article_sponsor_logo.py`
  (dry-run by default, lookup-first, `--apply` to create).
- `docs/gam_placement_injection.md` — the generalized technique behind the
  sponsor logo and the Apple FITO top banner: render any ad (incl. verbatim
  agency third-party tags) at an arbitrary article-DOM position with zero
  page changes. Carrier slot + priority-3 LI + SafeFrame-OFF wrapper
  creative that hides its slot, anchors on a DOM selector, and renders the
  payload in its own friendly iframe. Worked example:
  `scripts/setup_fito_top_banner.py` (970x250 between article title and
  video player). Covers the INACTIVE-until-order-reapproved, viewport/size
  eligibility, and ONE_OR_MORE roadblocking gotchas.
- `docs/betting_cpa.md` — Spinfinite betting/gambling CPA optimization
  (order 4068491190, IO1109). Covers the sub_id contract with Improvado,
  the macro-expansion learning (GAM doesn't expand `%`-prefixed macros in
  destinationUrl), the audience-segment picks, the test-LI batch plan,
  in-flight experiment state, and decision rules for the future engine.
  Companion scripts under `scripts/`: `betting_snapshot_source.py` (read-only
  diagnostic) and `betting_test_lis_batch.py` (the dry-run-by-default batch
  that creates the test LIs + creatives + LICAs and reduces the control goal).

## GAM facts (network 22541732127)
- The network has exactly **two yield groups**: `display` (id 680328) and
  `video` (id 680331). Both are **100% Open Bidding** — every ad source has
  `yieldIntegrationType: OPEN_BIDDING`. There is no Mediation traffic, so any
  per-buyer `YIELD_GROUP_*` reporting filtered to these groups is OB-only by
  construction.
- `YIELD_GROUP_CALLOUTS` is what the GAM UI calls "Ad requests" for a yield
  partner. Bid funnel goes: `YIELD_GROUP_CALLOUTS` → `YIELD_GROUP_BIDS` →
  `YIELD_GROUP_AUCTIONS_WON` → `YIELD_GROUP_IMPRESSIONS`.
- `HEADER_BIDDER_INTEGRATION_TYPE_NAME` is **incompatible with every
  `YIELD_GROUP_*` metric** in the v1 REST reporting API — adding it returns
  `REPORT_ERROR_CONSTRAINTS_INCOMPATIBILITY`. To distinguish OB from
  Mediation you have to inspect yield-group definitions via the SOAP
  `YieldGroupService` (not REST). In `v202605` the entity fields are
  `yieldGroupId` / `yieldGroupName` — not `id` / `name`.
- The service account **cannot create ad units** (`PERMISSION_DENIED` on
  `InventoryService.createAdUnits`) but can create native styles, line
  items, creatives, and LICAs. Out-of-page units forecast ~no inventory,
  so LIs targeting them need both `skipInventoryCheck` AND `allowOverbook`
  at create. Native-style macros are `[%Var%]` — bare `[Var]` is not
  substituted.
- **Newsletter native styles are fixed-size-per-slot, rendering one *fluid*
  native creative.** The Beehiv email tags (`gampad/ad?…&sz=WxH`) request a
  size; GAM renders the size-matched **native style** (HTML/CSS template) using
  the assets of a single **fluid (1×1, native-eligible) `TemplateCreative`** on
  one LI — so adding a new size = **add a native style at that size; no new
  creative / LI / LICA.** The Bulletin uses creative `138562096121` (fluid) on
  LI `7335266347`; custom per-slot styles share creative template `12544547`
  (Sponsored Content): `977473` 600×314 and `972672` 600×560 (#261 — added for
  the full un-clipped design, since the 314 frame clipped the headline/body/CTA
  stacked under the full-height image). Read / patch / clone styles with
  `scripts/update_native_style.py` via `update_native_style.yml`
  (`GAMClient.list_native_styles` / `update_native_style` /
  `create_native_style_from` / `get_creative_detail`); cloud sessions hold no
  GAM creds, so it runs through Actions like `archive_pli` (branch push = a
  read-only dump to the PR; a `[native-style-apply]` commit marker = the write).
- **Out-of-page slots need "Out of page"-size creatives, not 1x1** — a
  plain 1x1 CustomCreative created via API will not serve an OOP slot.
  LI placeholder: `creativeSizeType: INTERSTITIAL`; create the creative
  itself from the LI in the UI (size "Out of page"). The site's `?nwdemocr=`
  URL param sets a same-named GPT key-value for demo-gating test campaigns.
- One-off Actions-driven GAM pulls: `.github/workflows/pull_index_ob_requests.yml`
  is a template — it uses `secrets.GAM_SERVICE_ACCOUNT_JSON` /
  `secrets.GAM_NETWORK_ID` and posts the script's stdout as a PR comment.
  Copy it when you need to run a one-off pull from a cloud session that
  doesn't have GAM creds locally.
- **Active View reads ~0% viewable on any creative that renders in the
  parent DOM instead of the GPT slot iframe** — Mobkoi interscroller/
  uniscroller, the `addImageToHomepage`-style takeover customs, the Kia
  Homepage-Insight template. AV measures the iframe the tag hides (100%
  measurable / ~0% viewable) and DV instruments the same element, so
  `dv_attention` agrees and is equally meaningless there. The tell it's an
  artifact: healthy CTR, even more clicks than "viewable" impressions.
  In-frame renders measure organically on the same slots (ClipCentric
  Center Stage takeovers 58–67%, fluid native template 61.6%, site display
  baseline 75.4%) — **rendering in the measured iframe is the only way to
  move AV; there is no declare-viewable macro/API.** `%%VIEW_URL_UNESC%%`
  counts *impressions* for out-of-page creatives (delayed impression
  counting), NOT viewability — tested live 2026-06-11/12 (in-view watcher
  pinging it on LI 7310815861 creative 138562143597): viewable% unchanged,
  null result. GAM-report-native proxy if ever needed: in-view watcher →
  $0 tracking-LI pixel (watcher must live in the parent document — the
  breakout destroys the iframe realm and its observers/timers — and use
  AV's 30% threshold for elements >242,500 px²). Publisher-side fix that
  IS viable (DOM-verified 2026-06-12): Mobkoi *hides* the GPT iframe
  (display:none, not detached) and its unit's box == the slot div, so an
  **iframe mirror** (absolute transparent fill of the slot div) makes AV
  score real geometry — `docs/snippets/mobkoi_iframe_mirror_creative.html`.
  Mobkoi creatives are Celtra-built with advertiser-side DV `sid=mobkoi`.
  On-site preview + DOM forensics for any creative: dispatch
  `preview_mobkoi_dom.yml` (SOAP `getPreviewUrl` + headless Chromium;
  screenshots in artifacts). Debrief: `docs/mobkoi_viewability.md`.
  Per-LI AV pulls: dispatch `diagnose_mobkoi_viewability.yml` with any
  `line_item_ids`. Never set vCPM goals on breakout formats.

## Things to never commit
- `.env`, `*.db`, `*.csv`, `.streamlit/secrets.toml` (already in `.gitignore`).
- Magnite / GAM / Pubmatic credentials.
