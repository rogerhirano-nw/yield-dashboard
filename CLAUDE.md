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

## Things to never commit
- `.env`, `*.db`, `*.csv`, `.streamlit/secrets.toml` (already in `.gitignore`).
- Magnite / GAM / Pubmatic credentials.
