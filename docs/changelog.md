# Changelog

Chronological record of shipped work. Durable "how it works" detail lives in
`CLAUDE.md` (the feature/design sections); this file is the "what changed when,
and why" index, keyed by PR. Newest first.

## 2026-09-04 — Prebid bidders below the Active View baseline: diagnostics

- **Finding.** The *PreBid Display and Video* GAM report (Aug 14 - Sep 3)
  recomputed impression-weighted: **smilewanted 40.4%** viewable on 4.6M
  banner imps and **ogury 54.4%** on 2.8M (the report's pivot averages daily
  rates unweighted and buried ogury), **oms 56.3%** on 38k, and **onetag
  47.7%** on 138k in-stream video - against 78.7% banner / 86.5% video for
  every other bidder on the same slots. Flat across all 21 days, so
  structural rather than a regression. SmileWanted alone is 1.63M viewable
  impressions short of baseline; lifting it moves the whole Prebid banner
  number 75.8% -> 77.8%. All of them pay *above* average eCPM, so the answer
  is fix-the-render-or-the-mix, not block-the-bidder.
- **`dashboard_logic.viewability_mix_adjusted` + `av_threshold_pct`** (tested):
  splits a bidder's viewability gap into **MIX** (it wins on less-viewable
  slots/devices - a yield conversation) and **RENDER** (worse than peers in
  the *same* unit/device/size cell - a creative problem). Baselines are
  leave-one-out on both terms, so a bidder is never graded against its own
  bad impressions; `mix_gap + render_gap` reconstructs the total gap.
- **`scripts/prebid_viewability_audit.py`** (+ `prebid_viewability_audit.yml`):
  GAM Active View eligible/measurable/viewable by hb_bidder x ad unit x
  device x rendered size x format, run through the split, plus a per-day
  series and the measurable rate (which is where an unmeasurable render
  shows up, rather than in viewable%).
- **`GAMClient._run_report` gained an optional `filters` argument** (v1 REST
  `ReportDefinition.filters`, `(dimension, operation, values)` tuples, AND-ed)
  - needed because `hb_bidder` reaches reporting as the high-cardinality
  `KEY_VALUES_NAME` dimension and must be narrowed server-side. Existing
  callers are unchanged.
- **`scripts/prebid_render_forensics.py`** (+ `prebid_render_forensics.yml`):
  the Mobkoi DOM forensics adapted to wrapper demand, which GAM's on-site
  preview can't reach (the GAM creative is the Prebid universal creative, not
  the bidder's markup). Loads real **article** pages in headless Chromium,
  instruments GPT + Prebid before boot, scrolls with dwell time, and records
  per render: the pbjs `bidWon` winner (trusted over `hb_bidder` targeting,
  which only names the client-auction winner), GPT's own `impressionViewable`
  verdict, the in-view% timeline vs Active View's 50%/30% threshold, and
  iframe-vs-slot geometry.
- **First live finding:** ogury renders two ways - on `dfp-ad-sticky` a 1x1
  with the GPT iframe hidden at 0x0 (the Mobkoi breakout signature), and
  in-article a real 390x1094 well whose 65-67% in-view is simply what a
  large creative does under the 30% rule. Docs: `docs/prebid_viewability.md`.

## 2026-09-01 — beehiiv MCP server wired into the project config (#359)

- **Added `beehiiv` (remote HTTP, `https://mcp.beehiiv.com/mcp`) to the
  checked-in `.mcp.json`**, alongside the existing `supabase` server, so every
  Claude Code session opened on this repo is offered it rather than each person
  running `claude mcp add` into their own config. Servers are sorted
  alphabetically in the file for a stable diff.
- **Auth is browser OAuth, per-user, and never lands in the repo** — no API key
  in `.mcp.json` or `.env`. Consequence worth knowing before relying on it:
  **it cannot be authorized from a headless session** (Claude Code on the web,
  Actions runners), so the first authorization has to happen in an interactive
  local `claude` session via `/mcp`; until then the beehiiv tools are simply
  absent. Same constraint the Supabase connector already has (see
  `docs/supabase_disk_io.md`, where an unauthorized Supabase MCP blocked the
  measurement step).
- New **`## MCP servers`** section in `CLAUDE.md` records both servers, the
  transport/URL/auth for each, and the "edit `.mcp.json`, don't `claude mcp
  add`" preference.
- Config only — no client module, no refresh path, no dashboard surface. This
  does not wire beehiiv data into the cache or any tab; it just makes the
  server available to interactive sessions.

## 2026-08-03 — Supabase Disk IO Budget: diagnostic + first reductions

- **Supabase warned the prod project (`ltavpsikmmqmracvjtvk`, Micro compute)
  is depleting its Disk IO Budget** (email 2026-08-03). Nothing in the stack
  is individually huge, so the driver has to be measured on the instance, not
  guessed from code. Shipped in response:
  1. **`scripts/db_disk_io_report.py` + `db_disk_io_report.yml`** — a
     dispatchable, read-only diagnostic that prints Postgres's own IO
     accounting: DB/table/index sizes, cache-hit ratios (RAM vs disk reads),
     per-table disk blocks read + write churn (dead tuples, ins/del,
     autovacuum), temp-file spill, and the top statements by disk blocks
     read/written (pg_stat_statements). Optional `vacuum=1` input runs a
     lock-free `VACUUM (ANALYZE)` on every public table afterwards — the
     daily DELETE+append refresh pattern had never been followed by a
     manual vacuum.
  2. **Dashboard cache TTL 1h → 2h** (`_CACHE_TTL_SECONDS`) — the hourly
     cold reload (~25 table reads + 4 GROUP BY scans) was the dashboard's
     whole share of the budget; 2h halves it while still surfacing the
     4×/day intraday direct refreshes within ≤2h (their own cadence is 3h).
     Reverses half of the 2026-06-12 6h→1h premise ("neither constraint
     binds on Pro + Micro") — the disk-IO half was wrong.
  3. **`docs/supabase_disk_io.md`** — runbook: per-consumer IO table
     (dashboard reload / daily sweep / 4×-day direct refresh / health-check
     remediation sweeps / platform floor), how to read the report, and the
     levers cheapest-first (TTL, vacuum, converting `refresh_gam`'s
     DELETE+append to TRUNCATE, Micro→Small compute).
  Not done pre-emptively: `refresh_gam` churn conversion (verify no consumer
  depends on rows surviving a missed sweep first) and the compute upgrade
  (only if the report shows healthy cache-hit + modest churn, i.e. the floor
  is platform overhead).

## 2026-07-27 — CI: skip the $-advertiser one-off on dependabot PRs

- **`find_dollar_advertisers.yml` failed ("DATABASE_URL not set") on dependabot
  PR #342** — the dependabot actions-bump edited the workflow file itself,
  matching the workflow's own `paths` filter, and dependabot-triggered
  `pull_request` runs receive only Dependabot secrets, never repository
  Actions secrets, so `secrets.DATABASE_URL` came through empty. Fix: a
  job-level `if:` skips the run when the actor is `dependabot[bot]` (the
  check reports skipped/neutral instead of failure). Human PRs touching the
  script or workflow still run + comment as before; `workflow_dispatch` is
  unaffected. Deliberately NOT fixed by mirroring `DATABASE_URL` into
  Dependabot secrets — that would hand prod-DB access to dependabot-context
  runs for no benefit.

## 2026-07-27 — DV Attention: accept the XLSX report format

- **`dv_attention` had been stale since 6/29 because DV switched the emailed
  Attention report from CSV to XLSX** — the daily mails kept arriving
  (verified via `diagnose_dv_inbox.yml`: `Attention_<start>_<end>.xlsx`,
  ~1.8 MB, authenticated, downloadable) but `pull_dv_attention`'s
  `.csv`-only attachment filter skipped every one, so the sweep logged
  "No DV Attention CSV attachments found" for a month while the sibling
  IVT feed (still CSV) kept working. Fix in `dv_attention_client`: new
  `parse_dv_xlsx` (same sheet shape — preamble rows, then a header row
  starting with `Date`; cells arrive typed, and the float id path is the
  same #151 `.0` hazard as the CSV), the CSV/XLSX tail refactored into a
  shared `_normalize_dv_frame`, and the puller dispatches by extension.
  No refresh_cache logic change (`limit=2` + full-replace as before — the
  table only ever holds the latest ~8-day window, so no backfill needed).
  Regression test builds an in-memory DV-shaped workbook and asserts the
  normalized ids/date/index (`test_attention_xlsx_parses_and_normalizes_ids`).

## 2026-07-27 — TTD Luckyland retired (campaign ended)

- **The Luckyland Casino TTD lane is retired** (per Roger, 2026-07-27 — the
  campaign ended; the scheduled report had already stopped after ~7/1, and the
  forwarded "MonthtoDate v3" mails it left behind were the schema-change
  trigger for the daily `ttd_luckyland` recreate in the RLS-drift loop fixed
  in #343). Removed: `refresh_ttd` + `--mode=ttd` (`refresh_cache.py`), the
  Luckyland step in `refresh.yml`'s `ttd` job and in `refresh_ttd.yml`
  (now Chumba-only), the `ttd_luckyland fresh` health check row, the
  `idx_ttd_luckyland_date` index entry, and the dashboard's Luckyland
  Priority-flights card + drawer-CPA source (Chumba untouched; the section
  eyebrow now reads "1 betting flight"). Prod cleanup: run
  `DROP TABLE IF EXISTS public.ttd_luckyland;` in the Supabase SQL editor —
  nothing recreates it after this change (until dropped, the orphan table is
  still covered by the RLS-hygiene check and stays locked once the next
  health check runs). Kept as the pattern for the next TTD-reported flight (same as the Improvado
  retirement): `ttd_client.py` in full (needle consts, IdentityAlliance
  column handling) and the shared `_refresh_ttd_campaign` pipeline, which
  Chumba still uses.

## 2026-07-27 — RLS lockdown at table creation + remediation ordering

- **The health check's RLS auto-fix was being undone by its own sweep re-run,
  looping ❌ daily.** The TTD Luckyland scheduled report changed shape (the
  inbox now only has forwarded "MonthtoDate report v3" mails whose column set
  differs from `ttd_luckyland` and whose data ends 7/1), so every sweep hit
  `_refresh_ttd_campaign`'s schema-change path and DROP+recreated the table —
  and a freshly created Supabase table is REST-reachable (RLS off). The health
  check remediated in the wrong order: RLS fixed in-place *first*, sweep re-run
  *second* — the recreate re-opened RLS before the re-check, every run
  ("Still failing after remediation — needs a human", 2026-07-27). Two fixes:
  1. **`refresh_cache._lockdown_table`** — new helper that enables RLS +
     revokes anon/authenticated (the `docs/supabase_rls_lockdown.sql`
     end-state) on a just-created table, in the same transaction as the write.
     Called from `_safe_replace` and `_refresh_ttd_campaign` whenever the
     table was created (first deploy or schema-change drop). No-op on
     SQLite (local dev) and on plain Postgres without the Supabase roles.
  2. **`health_check.main` remediation order flipped** — sweep re-run first,
     in-place RLS fix second (it re-queries offenders, so it also catches
     drift the sweep just created).
  The same incident surfaced three upstream outages (all reported in the
  2026-07-27 digest): the Pubmatic token refresh 401s (data stale since 7/7),
  DV Attention emails arrive with no CSV attachment since ~6/29 (IVT
  unaffected), and the real TTD Luckyland scheduled report stopped after
  ~7/1 (only forwarded v3 mails remain, missing the IdentityAlliance
  conversion column).

- **Pubmatic token refresh was malformed — fixed against the vendor docs.**
  `pubmatic_client._call_refresh` never sent the required
  `Authorization: Bearer <previous access token>` header (the docs mandate
  it; omitting it is what 401'd every refresh once the token crossed the
  55-day refresh threshold ~7/10), and it discarded the **rotated
  refreshToken** the response returns, re-saving the old one — so even a
  successful refresh would strand the next cycle. Both fixed; `_call_refresh`
  now returns and persists the full new pair. Recovery paths on a failed
  refresh (refresh only works within the access token's 60-day validity and
  ours is past it): **(1)** if env `PUBMATIC_TOKEN` differs from the stored
  token, re-seed from the rotated secrets — a secret update alone
  self-heals, no manual `api_tokens` SQL; **(2)** if `PUBMATIC_PASSWORD` is
  set, **mint a brand-new pair via the first-time-setup `POST /token`**
  (`_call_generate` — invoked at most once per refresh cycle; Pubmatic
  disables the account at 200 generation attempts in 20 min, so never
  per-request). `refresh.yml` passes the optional `PUBMATIC_PASSWORD`
  secret; while it's absent the fallback is skipped. With the password
  secret set, the current outage recovers hands-free on the next sweep — no
  UI visit needed.

## 2026-07-23 — Hourly GAM clicks for per-hour CTR

- **The GAM hourly feed now pulls clicks alongside impressions so the cap
  digest can render per-hour CTR.** `run_hourly_report` (`gam_client.py`) — the
  `DATE × HOUR × LINE_ITEM_ID` report behind `--mode=gam_hourly` /
  `gam_campaigns_hourly` — added the **`AD_SERVER_CLICKS`** metric next to
  `AD_SERVER_IMPRESSIONS`, coerced to `int64` the same way. `refresh_gam_hourly`
  (`refresh_cache.py`) now writes an **`ad_server_clicks`** column on each hourly
  upsert; it `ALTER TABLE … ADD COLUMN ad_server_clicks bigint`s the existing
  `gam_campaigns_hourly` table once (guarded on the column not already existing)
  before the DELETE+append so the widened row lands cleanly. No consumer change —
  seller-comms auto-detects the column and renders per-hour CTR on the next cap
  digest.

## 2026-06-30 — Dashboard "today" derived in Eastern, not UTC (#339)

- **The dashboard showed delivery/flight dates a day ahead — `6/30` labels on
  the evening of `6/29` — because Streamlit Cloud runs in UTC.** `dashboard.py`
  derived "today" from a bare `date.today()` (and two
  `datetime.now(timezone.utc).date()` sites); after ~8pm ET, once UTC has
  crossed midnight, those resolve to the next calendar day, while the cached
  data is keyed to the GAM network tz (`America/New_York`) and so is the user's
  wall clock. No table actually held `6/30` rows (max dates were `6/27`–`6/29`),
  so this was a date *label/axis* artifact, not data. **Fix:** added a
  `_today_et()` helper (`datetime.now(_ET).date()`) and routed the 11
  display/pacing "today" sites through it — date-range presets (`_preset_range`),
  landing-risk window, pacing projection, "day N of M" flight labels, the
  drawer/small-multiples 7-day date rows, and the two stale-deal cutoffs.
  Mirrors the pull side, which already uses ET (`refresh_gam_hourly`,
  `gam_client._ts_to_date`). Intentionally left UTC: the `_pulled_at` timestamp
  and the clock-chip exception fallback. The `refresh_cache.py` "yesterday" pull
  windows stay UTC by design (they fire at 09:00 UTC / 05:00 ET — same calendar
  day, unaffected by the evening rollover). Rendering-only change; no
  `dashboard_logic` decision touched, `tests/test_dashboard_logic.py` green (74).

## 2026-06-23 — RLS hygiene canary in the health check (#322)

- **New source tables kept drifting into Supabase RLS-disabled; the daily
  health check now catches and auto-fixes it.** Supabase's security advisor
  flagged 17 public tables (TTD, DV, `gam_deal_bid_daily`, the `*_metadata`
  tables, `opensincera_*`, `pmp_last_bid_date`, the GAM rollups) with
  Row-Level Security off. They were **not** actually readable — the 2026-05
  `docs/supabase_rls_lockdown.sql` had already `REVOKE`d all anon/authenticated
  grants, and an anon REST probe returns `42501 permission denied` — so this
  was a **defense-in-depth gap, not an open door** (the advisor checks RLS
  only, not grants). Root cause is drift: the lockdown's `ALTER DEFAULT
  PRIVILEGES` auto-revokes grants on new tables but nothing auto-enables RLS,
  so every new source arrives RLS-off. **Fixes:** (1) enabled RLS on the 17
  tables in prod (migration `enable_rls_on_remaining_17_public_tables`);
  (2) added a fifth health-check invariant — **`public RLS hygiene`**: any
  public table with RLS off or an anon/authenticated grant fails it, and
  (unlike the freshness/sweep checks) it is auto-remediated **in-place**
  (enable RLS + revoke grants, mirroring the lockdown SQL) rather than by a
  refresh sweep — a sweep can't fix RLS and in fact creates the tables that
  drift. Reported in the daily digest; `HEALTH_AUTO_REMEDIATE=0` disables it
  like the sweep path. Pinned by `test_rls_hygiene_*` in
  `tests/test_health_check.py`.

## 2026-06-22 — Sponsor-logo Active View un-clip (#313)

- **Article sponsor-logo viewability was a measurement artifact, now fixed in
  GAM.** LI 7336465381 (Infiniti Newsmakers "Presented by" strip, oop1) read
  **~1% viewable / mostly 0% measurable** for weeks. Root cause, found by
  inspecting the live JT Batson article (`article_id=12010430`) in headless
  Playwright: the oop1 GPT iframe **survives** and the carrier-reposition
  **works** (the long-held "hydration destroys the iframe" theory was wrong) —
  but GPT forces that iframe to **~150px** while the carrier clipped it
  (`overflow:hidden`) to the logo's **~24px**. Active View is
  IntersectionObserver-based and respects the ancestor clip, so it saw only
  **16%** in view — under the 50% bar — and booked every impression
  not-viewable. **Fix:** size the carrier to the iframe's real height
  (`overflow:visible`), so AV measures the whole in-view iframe. Verified live
  with an IntersectionObserver (AV's own geometry): in-view ratio **0.16
  clipped → 1.00 un-clipped**, logo render unchanged, then re-verified on a
  fresh serve of the deployed creative. Impressions (`AD_SERVER_IMPRESSIONS`)
  **and** viewability (Active View) now both come from the one creative on the
  one LI via GAM's normal report — no beacon, no second counter (OMD declined
  to provide a viewable pixel, so GAM AV is the measurement of record). New
  snippet `docs/snippets/article_sponsor_logo_creative.html` (only `syncCarrier`
  changed); applied via `scripts/apply_sponsor_logo_av_fix.py` (dry-run default,
  `APPLY=true`) / `apply_sponsor_logo_av_fix.yml`; read-only diagnostics
  `scripts/inspect_sponsor_logo_{creative,li}.py`. Confirm on the next day's
  `gam_campaigns.viewable_imps_1d` for LI 7336465381 (AV lags ~1 day). The
  dashboard needs no change — it already reads the AV columns.

## 2026-06-22 — Editorial landing polish

- **Priority-flights cards: side-by-side + $150 CPA goal.** The two TTD
  acquisition cards (Luckyland / Chumba) now render **side-by-side** on desktop
  (`st.columns(2)`; a `.nw-ttd-wrap .nw-na` override lifts the shared 760px cap so
  each fills its column — the cap stays for the single-column Needs-attention /
  PMP-signals cards), and collapse to stacked on mobile. Each card is **graded
  against a CPA goal** (new `ttd_cpa_goal` setting, default **$150**, editable in
  Settings → Direct): the CPA hero shows a `✓ $X under` / `✗ $X over` verdict
  (`dl.cpa_goal_delta`, reusing the green/red delta colors), and the Daily CPA
  chart draws a dashed reference line at the goal (`_ttd_trend_svg(ref=…)`, folded
  into the y-scale; default off so the Direct drawer's reuse is unchanged). Pinned
  by `test_cpa_goal_delta`.
- **Direct LI end dates were a day late (UTC→ET fix).** The drawer's Flight cell
  showed a 6/30 flight ending **7/1**. `gam_client._ts_to_date` read GAM's
  line-item `end_time` in **UTC**, but GAM ends a line at 23:59 in the network
  tz (America/New_York) — so `2026-06-30T23:59 ET` = `…T03:59Z` took the UTC
  date (7/1). Now converted to `America/New_York` before `.date()`. Only ends
  rolled (starts are 00:00 ET, same UTC day); PMP deal dates were already fine
  (SOAP path reads y/m/d directly). The derived Completed/Delivering status,
  which uses the same date, was also a day late. Pinned by `tests/test_gam_dates.py`;
  prod repopulated via a Direct refresh.
- **Gambling CPA join switched to `deal_id` (robust).** The per-LI CPA join is
  now keyed on the **GAM/TTD shared `deal_id`** instead of brittle name tokens.
  A throwaway GAM diagnostic confirmed GAM's report **`DEAL_ID` dimension equals
  the TTD feed's `deal_id`** for our PG flights (live: LI 7328197875 → deal
  4211124 = TTD Chumba; 7315575731 → 4215587 = TTD Luckyland) — exactly the VGW
  Casino-Gamblers LI whose CPA block wasn't showing, because its name
  (`Chumba-Casino-Gamblers` / `320x50`) couldn't reduce to the TTD ad_group via
  `cpa_join_key`. `gam_campaigns` now carries a **`deal_id`** column
  (`GAMClient.run_li_deal_map_report` — a separate `[LINE_ITEM_ID, DEAL_ID]`
  report, since DEAL_ID is incompatible with the delivery report's metric set —
  left-merged in `refresh_gam`), and `dl.ttd_cpa_for_deal` aggregates the
  matching TTD rows. The name-token join (`cpa_join_key` / `ttd_cpa_for_li`) is
  kept as a fallback. Prod backfilled for the live gambling LIs so the blocks
  show immediately. Pinned by `test_ttd_cpa_for_deal`. (`PROGRAMMATIC_DEAL_ID`
  isn't a valid v1 dimension; SOAP ProposalLineItem carries no deal-id field —
  the report dimension is the only source.)
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
