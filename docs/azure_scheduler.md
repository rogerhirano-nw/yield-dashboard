# Scheduler: Azure Logic Apps (replaced cron-job.org)

Since 2026-11-27, every timed GitHub workflow in the ad ops repos is started by
an **Azure Logic App** in Newsweek's subscription, not by cron-job.org (whose
account was Roger's; its 9 jobs are now *Inactive*, kept only as a reference).

Each Logic App fires on an Eastern-time schedule (DST handled by Azure), reads
the GitHub token from Key Vault with its own managed identity, and calls
`POST /repos/<owner>/<repo>/actions/workflows/<file>/dispatches` with
`{"ref":"main"}` — exactly what cron-job.org did.

| | |
|---|---|
| Resource group | `rg-adops-supermetrics-prod` (sub "SoftCoice CSP - Newsweek Azure"), Central US |
| Logic Apps | `la-sched-*` (Consumption; ~pennies/month) |
| Key Vault | `nw-adops-sched-kv`, secret `github-dispatch-token` (access-policy model; each Logic App identity has *get secret*) |
| Template | `infra/scheduler/scheduler.json` (ARM) — the source of truth for the schedule |

## Schedule (all times America/New_York)

| Logic App | Repo | Workflow | When |
|---|---|---|---|
| `la-sched-yield-refresh-all` | yield-dashboard | `refresh.yml` | daily 05:00 |
| `la-sched-yield-refresh-direct` | yield-dashboard | `refresh_direct.yml` | every hour at :45 |
| `la-sched-yield-health-check` | yield-dashboard | `health_check.yml` | 05:45 and 09:45 (was launchd on Roger's Mac) |
| `la-sched-seller-cap-digest` | seller-comms | `cap_digest.yml` | every hour at :00 |
| `la-sched-seller-daily-report` | seller-comms | `daily_seller_report.yml` | daily 08:00 |
| `la-sched-seller-ttd-cpa` | seller-comms | `ttd_cpa_report.yml` | daily 08:00 |
| `la-sched-seller-weekly-deal-health` | seller-comms | `weekly_report.yml` | Mondays 09:00 |
| `la-sched-applenews-daily` | apple-news | `daily-report.yml` | daily 10:00 |
| `la-sched-applenews-monthly` | apple-news | `monthly-report.yml` | 3rd of the month 10:00 |

Logic Apps can't do `frequency: Hour` with a fixed minute, so the hourly jobs are
`Day` with all 24 hours listed.

## The GitHub token — the one thing still tied to a person
The token is a fine-grained PAT ("azure-logic-apps-scheduler", Actions:
read & write on yield-dashboard / seller-comms / apple-news), created
2026-11-27 under **Roger's** GitHub account, limited to those three repos,
**no expiration date**. It stops working when Roger's GitHub access goes away
— after that every scheduled job silently stops starting, and only the health
check's 18:00 UTC GitHub-native cron would still run and flag the stale data. When the repos move to the
Newsweek GitHub org:

1. Redeploy with the new owner (URLs include it; a POST to the old URL won't
   follow GitHub's rename redirect reliably):
   ```bash
   az deployment group create -g rg-adops-supermetrics-prod -n github-scheduler \
     -f infra/scheduler/scheduler.json -p location=centralus state=Enabled githubOwner=<newsweek-org>
   ```
2. Have a Newsweek-owned GitHub account create a fine-grained token (Actions:
   read & write on the three repos) and replace the secret — the Logic Apps pick
   it up on their next run, nothing else changes:
   ```bash
   az keyvault secret set --vault-name nw-adops-sched-kv -n github-dispatch-token --file token.txt
   ```

## Alerts
Each Logic App has a metric alert `alert-la-sched-<job>-failed` (any failed run
in the last hour, checked every 15 min, severity 2) that emails
**adops@newsweek.com** through action group `ag-adops-email`. A failure almost
always means the GitHub token was revoked/expired or a repo moved. Related
recipients, all AdOps since 2026-11-27: yield-dashboard vars `HEALTH_DIGEST_TO`
(health check) and `REFRESH_ALERT_TO` (sweep failures), apple-news var
`ALERT_TO` (report failures).

## Operating it
- **Did it fire?** Portal → the Logic App → *Runs history*, or the GitHub
  Actions list (runs show as `workflow_dispatch`). A run whose
  `Dispatch_workflow` step failed with 401/404 means the token expired or a repo
  moved. The token step's outputs and the dispatch inputs are marked secure, so
  the token never appears in run history.
- **Pause a job:** disable its Logic App (portal, or
  `az rest --method post --url https://management.azure.com/<logic-app-id>/disable?api-version=2019-05-01`).
- **Change a time / add a job:** edit `jobs` in `scheduler.json` and redeploy
  (command above, `state=Enabled`). New Logic Apps get a new identity — grant
  it `get` on the vault: `az keyvault set-policy -n nw-adops-sched-kv --object-id <principalId> --secret-permissions get`
  (the deployment's `principalIds` output lists them).
- **Missed runs aren't replayed.** The health check (05:45/09:45 + its own
  18:00 UTC GitHub cron fallback) re-runs a missed or failed sweep.
- **Run one now:** portal → Logic App → *Run trigger*, or
  `gh workflow run <file> -R <owner>/<repo>`.
