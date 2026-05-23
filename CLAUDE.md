# Claude Code notes for yield-dashboard

See `README.md` for project overview, files, and quickstart.

## Conventions
- Python (Streamlit dashboard + per-source clients). Cache layer is SQLite locally, Postgres in prod (`DATABASE_URL` Supabase).
- One client module per data source (`*_client.py`), one `refresh_<source>` function in `refresh_cache.py`, called from `main()`.
- Pull yesterday's data, not today's — same-day data has latency.

## Data sources currently wired
When auditing or adding data, the four production sources are:

| Source | Client module | Cache tables (prefix) | Provenance |
|---|---|---|---|
| **Magnite DV+** | `client.py` (`MagniteClient`) | `magnite_*` | SSP delivery + deals |
| **Google Ad Manager** | `gam_client.py` (`GAMClient`) | `gam_*` (campaigns, pmp_deals, creatives, lica, …) | Direct delivery + PMP/PA/PD/PG |
| **Pubmatic** | `pubmatic_client.py` (`PubmaticClient`) | `pubmatic_*` | PMP deal report |
| **OpenSincera** | `opensincera_client.py` (`OpenSinceraClient`) | `opensincera_*` (ecosystem, publishers, adsystems, mapping_modules) | TTD's sell-side transparency / inventory metadata. Added 2026-05-22 (PR #44 + #46). Powers the OpenSincera dashboard tab with a Newsweek-vs-peers scorecard. |

`refresh_cache.py main()` accepts `--mode={all,direct,opensincera}`. Default is `all` (full sweep). Each source has a corresponding `refresh_<source>` function callable individually for ad-hoc work.

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
