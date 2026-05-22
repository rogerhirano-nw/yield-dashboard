# Claude Code notes for yield-dashboard

See `README.md` for project overview, files, and quickstart.

## Conventions
- Python (Streamlit dashboard + per-source clients). Cache layer is SQLite.
- One client module per data source (`*_client.py`), one `refresh_<ssp>` function in `refresh_cache.py`, called from `main()`.
- Pull yesterday's data, not today's — same-day data has latency.

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
