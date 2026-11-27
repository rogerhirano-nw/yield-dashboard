# Dashboard on Azure App Service

Moved off Streamlit Community Cloud (2026-11-27). The Cloud app
(`newsweek.streamlit.app`) lived in Roger's personal Streamlit/GitHub account,
so it stops existing when he leaves. Same code (`dashboard.py`) now runs in
Newsweek's Azure subscription, behind Newsweek's Microsoft login.

| | |
|---|---|
| URL | https://nw-adops-yield-dashboard.azurewebsites.net |
| Web app | `nw-adops-yield-dashboard` (Linux, Python 3.12) |
| Plan | `nw-adops-yield-plan`, **B1**, ~$13/mo, **East US 2** |
| Resource group | `rg-adops-supermetrics-prod` (sub "SoftCoice CSP - Newsweek Azure") |
| Database | `nw-adops-yield-db` (Central US) — see `docs/azure_db_migration.md` |
| Login | Entra (App Service auth, classic). App registration `nw-adops-yield-dashboard`, appId `515ccd55-c974-45a8-87cf-aa37d4c726c9`; client secret expires ~2028-11 |

**Why East US 2:** the subscription has **zero App Service quota in Central US**
(B1/S1/P0v3/F1 all refused). Cross-region to the DB adds ~25 ms per query,
which the 2h `st.cache_data` hides.

## Configuration
- Startup command: `python -m streamlit run dashboard.py --server.port 8000 --server.address 0.0.0.0 --server.headless true`
- Web sockets **on** (Streamlit needs them), Always On **on** (no sleeping).
- App settings: `DATABASE_URL` (same Azure Postgres URL as the GitHub secret),
  `SCM_DO_BUILD_DURING_DEPLOYMENT=true` (Oryx installs `requirements.txt` on
  deploy), `WEBSITES_PORT=8000`.
- `dashboard.py` reads `st.secrets["DATABASE_URL"]` first and falls back to the
  environment, so no `secrets.toml` is needed on App Service.
- `.streamlit/config.toml` (theme) ships with the code. Licensed fonts in
  `static/fonts/` are gitignored, so App Service uses the Georgia/Helvetica
  fallbacks unless someone uploads them.

## Deploys
`.github/workflows/deploy_dashboard_azure.yml` deploys `main` on every push
(docs/markdown-only pushes skipped; also `workflow_dispatch`). It zips tracked
files and pushes them with `azure/webapps-deploy`, authenticated by the web
app's **publish profile** in secret `AZURE_WEBAPP_PUBLISH_PROFILE`.
SCM basic-auth publishing was enabled on **this web app only** for that
(Azure disables it by default) — Roger's account can't create role
assignments, which the keyless OIDC route needs. To harden later, ask IT to
grant a deploy identity *Website Contributor* on the web app, switch the
workflow to `azure/login` with OIDC, and turn basic publishing back off.

Manual deploy (from a checkout with `az` logged in):
```bash
git archive --format=zip -o /tmp/app.zip origin/main
az webapp deploy -g rg-adops-supermetrics-prod -n nw-adops-yield-dashboard --src-path /tmp/app.zip --type zip
```
The CLI often reports a 504 while the build keeps going — check
`az webapp log deployment list …` (status 4 = success) before retrying.

First page load after a restart/deploy takes ~60s (cold cache: DV aggregates);
after that it's cached for 2h.

## Rotating things
- **Publish profile** (if leaked): `az webapp deployment list-publishing-profiles … --xml`
  after resetting it in the portal (Overview → Download/Reset publish profile),
  then `gh secret set AZURE_WEBAPP_PUBLISH_PROFILE`.
- **Login secret** (expires ~2028-11): `az ad app credential reset --id 515ccd55-…`
  then `az webapp auth-classic update … --aad-client-secret <new>`.
- **DB password**: update both the `DATABASE_URL` app setting here and the
  GitHub secret.
