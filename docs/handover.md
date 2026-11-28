# Handover — ad ops automation (written 2026-11-28, before Roger Hirano leaves)

One page for whoever inherits this. What runs, where, who owns each piece, and
what must change hands **before Roger's accounts close**. Detail lives in the
linked runbooks; `CLAUDE.md` is the full engineering reference.

## What runs, and when (all times ET)

| When | What | Where it runs | Started by |
|---|---|---|---|
| 05:00 daily | Full data refresh (GAM, Magnite, PubMatic, DV, TTD, OpenSincera) | GitHub Actions `yield-dashboard/refresh.yml` | Azure Logic App `la-sched-yield-refresh-all` |
| :45 hourly | GAM direct-campaign refresh | `yield-dashboard/refresh_direct.yml` | `la-sched-yield-refresh-direct` |
| 05:45, 09:45 | Data health check → email to adops@ (auto re-runs a failed refresh) | `yield-dashboard/health_check.yml` | `la-sched-yield-health-check` (+ GitHub cron 18:00 UTC fallback) |
| :00 hourly | Ivy's campaign tracker (email + Teams) | `seller-comms/cap_digest.yml` | `la-sched-seller-cap-digest` |
| 08:00 daily | Seller campaign report, TTD CPA report | `seller-comms` | `la-sched-seller-daily-report`, `la-sched-seller-ttd-cpa` |
| Mon 09:00 | Weekly deal health | `seller-comms/weekly_report.yml` | `la-sched-seller-weekly-deal-health` |
| 10:00 daily / 3rd monthly | Apple News reports | `apple-news` | `la-sched-applenews-daily` / `-monthly` |
| 07:15, 07:52 daily | PepsiCo media-plan card, IO1170 card (Supermetrics → Teams) | Claude routines → `seller-comms/campaign_feed.yml` | Claude routines on Roger's claude.ai account |
| always | Yield dashboard | Azure App Service `adops` → https://adops.azurewebsites.net (Newsweek login) | auto-deploys from `yield-dashboard` `main` |

Runbooks: `docs/azure_scheduler.md` (schedules), `docs/azure_dashboard.md`
(dashboard), `docs/azure_db_migration.md` (database), seller-comms `CLAUDE.md`
(campaign feeds, emails), apple-news `CLAUDE.md`.

## Where things live (Newsweek-owned)
- **Azure** (sub "SoftCoice CSP - Newsweek Azure", RG `rg-adops-supermetrics-prod`):
  Postgres `nw-adops-yield-db` (DB `yield`); App Service plan `nw-adops-yield-plan`
  + web app `adops`; Logic Apps `la-sched-*` with failure alerts → adops@;
  Key Vault `nw-adops-sched-kv` (GitHub tokens, DB password + URL); storage
  `nwadopsyieldarchive` (final Supabase backup).
- **Entra apps**: `nw-adops-yield-dashboard` (dashboard login), `yield-reporting-automation`
  (reporting@ mail — waiting on admin consent).
- **Alerts** go to **adops@newsweek.com** (health check, refresh failures,
  Logic App failures, apple-news failures).

## ⚠️ Tied to Roger — must move before his accounts close

| # | Item | What breaks if it isn't moved | What to do |
|---|---|---|---|
| 1 | **GitHub repos** `yield-dashboard`, `seller-comms`, `apple-news`, `adops-gam-reports` are under Roger's personal account | Everything (code, workflows, secrets) | Transfer to a Newsweek GitHub org; then redeploy the scheduler with the new owner (`docs/azure_scheduler.md`) |
| 2 | **Scheduler GitHub token** (Roger's PAT, Key Vault `github-dispatch-token`) | Every scheduled job silently stops starting | Newsweek-owned account creates a fine-grained token (Actions RW on the repos) → replace the Key Vault secret |
| 3 | **Power Automate flows** (17, all owned by Roger): "Report mail as AdOps" (all seller-comms email), the apple-news copy, Teams card flows (Ivy, PepsiCo, Chumba, IO1170) | All seller emails, apple-news, health/alert emails, Teams cards | A staying colleague is added as co-owner of each flow and swaps in their own Outlook/Teams connections. Long-term: switch mail to reporting@ via Graph (#430, apple-news `GRAPH_MAILBOX`) once IT consents |
| 4 | **Claude routines** (PepsiCo feed, IO1170 card) on Roger's claude.ai account | The two Supermetrics cards | New owner recreates them from their instructions (seller-comms `CLAUDE.md`, "Campaign feeds") |
| 5 | **Supermetrics** — Roger is the only member; license ends **2026-12-15** | Supermetrics data | m.lukac (Owner) / l.eccleston (Admin) accept their invites; move billing; reconnect Meta/LinkedIn/GA4/IG/FB/TikTok with their own logins |
| 6 | **agentmail** inbox `newsweek@agentmail.to` (DV + TTD reports arrive here) | DV attention/IVT and TTD Chumba data | Point DV and TTD report schedules at reporting@ (needs #430 + IT consent); DV/TTD schedules themselves run under Roger's users there |
| 7 | **apple-news Gmail sender** `campaign.insights.newsweek@gmail.com` (fallback sender) | apple-news fallback | Put its password + 2FA in Newsweek's vault |
| 8 | **GAM service account** — confirm the Google Cloud project is Newsweek-owned | All GAM data | Check project ownership in GCP |
| 9 | **Vendor API logins** (Magnite, PubMatic, OpenSincera) | Those data sources | Move to shared accounts, update repo secrets |
| 10 | **Azure CLI deploys** — basic-auth publish profile on `adops` | Only manual deploys | Optional hardening: IT grants a deploy identity Website Contributor, switch to OIDC |

## Waiting on IT (one request covers it)
1. Admin consent for **`yield-reporting-automation`** (Mail.Read + Mail.Send) scoped to reporting@ — moves all email off Roger's flows and agentmail.
2. Tenant-wide admin consent for **`nw-adops-yield-dashboard`** — removes the per-user consent step (iPhone sign-in fails at it).
3. A **service principal** with rights on `nw-adops-yield-db` so GitHub jobs can open/close firewall rules per run — then the DB can stop accepting all IPs.

## When something is red
- **Health email ❌**: it already retried the refresh once. Read the ❌ lines;
  the "Technical details" section names the table. Re-run: `gh workflow run refresh.yml -R <owner>/yield-dashboard`.
- **Logic App failure alert**: a scheduled job couldn't be started — usually the GitHub token (item 2) or a moved repo.
- **No email at all**: Power Automate flow (item 3) or agentmail; check the flow's run history (every run shows "Failed" by design — open it and check the *Send an email* step).
- **Dashboard down**: `az webapp restart -g rg-adops-supermetrics-prod -n adops`.
