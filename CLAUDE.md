# Claude Code notes for yield-dashboard

See `README.md` for project overview, files, and quickstart.

## Conventions
- Python (Streamlit dashboard + per-source clients). Cache layer is SQLite locally, Postgres in prod (`DATABASE_URL` Supabase).
- One client module per data source (`*_client.py`), one `refresh_<source>` function in `refresh_cache.py`, called from `main()`.
- Pull yesterday's data, not today's — same-day data has latency.
- **Never push directly to `main`.** Branch protection enforces PRs for everyone including admins. Always work on a branch and open a PR — even for docs-only changes. README/CLAUDE.md updates go in the same PR as the code they describe.

## Data sources currently wired
When auditing or adding data, the four production sources are:

| Source | Client module | Cache tables (prefix) | Provenance |
|---|---|---|---|
| **Magnite DV+** | `client.py` (`MagniteClient`) | `magnite_*` | SSP delivery + deals |
| **Google Ad Manager** | `gam_client.py` (`GAMClient`) | `gam_*` (campaigns, pmp_deals, creatives, lica, …) | Direct delivery + PMP/PA/PD/PG |
| **Pubmatic** | `pubmatic_client.py` (`PubmaticClient`) | `pubmatic_*` | PMP deal report |
| **OpenSincera** | `opensincera_client.py` (`OpenSinceraClient`) | `opensincera_*` (ecosystem, publishers, adsystems, mapping_modules) | TTD's sell-side transparency / inventory metadata. Added 2026-05-22 (PR #44 + #46). Powers the OpenSincera dashboard tab with a Newsweek-vs-peers scorecard. |
| **DoubleVerify Attention** | `dv_attention_client.py` (`pull_dv_attention`) | `dv_attention` | DV Pinnacle "Authentic Attention" metrics per line item — 100-baseline indices (Attention / Engagement / Exposure / Intensity / Prominence / User Presence / Ad Interaction / View Presence) plus DV's view of viewability. Ingested via email: DV team mails the daily CSV to `newsweek@agentmail.to`, we poll the inbox via agentmail's v0 API and parse the attachment. Surfaces as the "Attention" column on Direct + PMP tables. Subject filter: `Unified Analytics Report: Attention Metrics`. Added 2026-05-24. |
| **DoubleVerify IVT** | `dv_ivt_client.py` (`pull_dv_ivt`) | `dv_ivt` | DV Pinnacle invalid-traffic classification rows (Valid Traffic / Fraud/SIVT / Fraud/GIVT) per line per day, with `Monitored Ads` impression counts. Same email pipeline as DV Attention; subject filter: `Unified Analytics Report: IVT`. The dashboard computes **impression-weighted IVT%** per MRC standard: `Σ Monitored Ads (Fraud rows) / Σ Monitored Ads (all rows)`. Surfaces as **separate "SIVT" and "GIVT" columns** on Direct + PMP tables (MRC distinction: SIVT = data center / bot fraud / hijacked devices / emulators / app + site fraud, hard to detect; GIVT = self-identifying bots / declared crawlers, standard detection). Color bands tuned to industry IVT thresholds: green <1%, amber 1-3%, red ≥3%. Added 2026-05-24. |

`refresh_cache.py main()` accepts `--mode={all,direct,opensincera}`. Default is `all` (full sweep). Each source has a corresponding `refresh_<source>` function callable individually for ad-hoc work. DV Attention is folded into the full sweep — no `--mode=dv_attention` flag because the agentmail poll is cheap (~3s + however long DV's CSV is to parse).

For one-off DV backfills (manually downloaded Pinnacle CSV), use `scripts/seed_dv_attention.py /path/to/file.csv`.

## Outbound daily digests

| Digest | Script | Workflow | Recipients (var) | Subject |
|---|---|---|---|---|
| Betting CPA (Spinfinite, IO1109) | `betting_daily_update.py` | `.github/workflows/betting_daily_digest.yml` | `BETTING_DIGEST_TO` (var) / `BETTING_DIGEST_CC` (var, optional) | `Newsweek Betting CPA digest — <yesterday>` |

Same outbound `POST /v0/inboxes/<inbox_id>/messages/send` pattern as `apple-news/daily_report.py`. Triggered externally by cron-job.org via `workflow_dispatch` (GitHub-native `schedule:` drifts hours late). Suggested cadence: 09:30 America/New_York daily — half an hour after the 09:00 refresh sweep so the freshest Improvado report is already in `betting_conversions`.

Local dry-run (no DB / no send): `python betting_daily_update.py --dry-run`. Requires `DATABASE_URL` in env for the DB read; the `--dry-run` flag skips the send only.

CPA target defaults to $150/FTP. Override with `BETTING_CPA_TARGET` repo variable.

## Streamlit Cloud deploy
**Production deploys from `main`** (since ~2026-05-22). Previously was pinned to `mac-studio`, but that branch is no longer the deploy target. Push to main → Cloud auto-redeploys within ~60s. Don't merge main → mac-studio out of habit unless someone has explicitly re-pointed Cloud back at it.

## Subsystems with their own docs
- `docs/confiant_blocklist.md` — weekly Confiant -> GAM Protection sync. Not a
  standard SSP client; runs locally via launchd (GAM Protections has no API,
  so Playwright drives the UI). See doc for the Google-UI-changed-and-broke-it
  recovery flow.

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
- One-off Actions-driven GAM pulls: `.github/workflows/pull_index_ob_requests.yml`
  is a template — it uses `secrets.GAM_SERVICE_ACCOUNT_JSON` /
  `secrets.GAM_NETWORK_ID` and posts the script's stdout as a PR comment.
  Copy it when you need to run a one-off pull from a cloud session that
  doesn't have GAM creds locally.

## Things to never commit
- `.env`, `*.db`, `*.csv`, `.streamlit/secrets.toml` (already in `.gitignore`).
- Magnite / GAM / Pubmatic credentials.
