# Claude Code briefing — yield-dashboard

Comprehensive handoff for any Claude (or human) session opening this repo cold.
For the user-facing project overview see `README.md`.

## What this repo is

Python toolkit that pulls ad revenue from multiple SSPs into a local SQLite
cache and serves it through a Streamlit dashboard. Live in production —
Newsweek's primary internal yield dashboard.

Sources currently wired: **Magnite DV+**, **Google Ad Manager** (direct +
PMP), **Pubmatic** (PMP). A separate `apple-news` repo handles Apple News
publisher reporting; this repo does not.

## Layout

| Path | What it is |
|---|---|
| `client.py` | `MagniteClient` — auth, create/poll/paginate, 429 backoff. |
| `gam_client.py` | `GAMClient` — direct delivery, pacing, PMP deals. |
| `pubmatic_client.py` | `PubmaticClient` — PMP deal report. |
| `refresh_cache.py` | Scheduled entrypoint. Pulls all sources into SQLite. |
| `dashboard.py` | Streamlit dashboard. Single ~330KB file (refactor candidate). |
| `confiant_client.py` + `confiant_blocklist.py` + `gam_blocklist_ui.py` | Weekly Confiant → GAM Protection sync via Playwright (GAM Protection has no API). |
| `deal_health/` | Weekly deal-health email report module (rendered HTML + CSV). |
| `tests/` | Pytest suite. Most coverage is on `deal_health` rendering. |
| `settings.json` | Runtime config (deal_source defaults per SSP, threshold colors, etc.). Tracked in git. |
| `.claude/settings.json` | Shared Claude Code settings — checked in so both laptops share them. Other `.claude/*` state is gitignored. |
| `.streamlit/secrets.toml` | Streamlit Cloud secrets file. **Gitignored.** |
| `.env` | Local credentials. **Gitignored.** |

## Branches and deployment

- `main` — the trunk. Day-to-day commits land here (via PR per branch protection).
- `mac-studio` — what **Streamlit Cloud actually deploys from**. To ship a fix
  to production, merge `main → mac-studio` and push. `mac-studio` therefore
  trails `main` in flat-commit count but is ahead in merge commits.
- `feat/*`, `fix/*`, `ops/*`, `diagnose/*` — feature branches, opened as PRs
  against `main`.

**Branch protection on `main` requires PRs.** Even when working solo, push to a
feature branch and open a PR rather than directly to `main`. Admin can bypass
but GitHub will flag it.

## Multi-laptop workflow — critical

Roger uses **two laptops**, both cloning this repo. Both are active dev
environments treated as separate "devs" creating PRs.

- **Always `git fetch` before working.** The other laptop is an active
  collaborator, not a mirror. Don't assume your local state is current.
- **Uncommitted edits can get clobbered by an auto-sync.** Batch
  `edit → git add → git commit → git push` in a single Bash call. Don't leave
  a half-edited working tree across turns.
- **Git identity is per-repo** (`git config user.email` set locally). No
  global git config on either laptop — at least one is a Newsweek work Mac.

## Cron and scheduling

All scheduled workflows fire via **cron-job.org → `workflow_dispatch`**, not
GitHub native `schedule`. GH-native cron was queuing scheduled runs 5–6 hours
late under daytime load and we switched everything off it. The migration is
in commit history (`ops: migrate both scheduled workflows from GitHub cron to
cron-job.org`).

Active scheduled workflows:
- `refresh.yml` — daily full cache refresh
- `refresh_direct.yml` — twice-daily direct campaign refresh (11 + 15 ET)
- `weekly_report.yml` — weekly deal health email
- `export_gam_deals.yml` — GAM deals CSV export

Provisioning script lives at `scripts/provision_cronjob.sh` (per recent
ops commits).

## Domain rules baked into the code

### Seller (AE code) derivation
Direct campaigns identify the seller via the **AE code** convention in the
order/line-item name. The mapping logic lives in the dashboard's seller
column. Some deals (e.g. **VGW**) intentionally have **no AE** — don't
treat blank as a bug. See `settings.json` `account_manager_map` and the
Configure → Account Managers UI.

### Data freshness window
The SQLite cache tables hold **only the last 7 days**, not lifetime. This is
deliberate: storage stays small and the dashboard reads fast. Implication:
**`SUM(impressions)` ≠ campaign-to-date.** Campaign-to-date numbers come from
the live `_ctd` columns in the API response, not from aggregating the cache.

### Deal Source defaults
GAM rows are always **Publisher** (business rule). For other SSPs, blank
`Deal Source` values get filled in from `settings.json` `deal_source_default`
(keyed by SSP name). The Configure UI lets the user override on a per-deal
basis; defaults apply to anything not overridden.

### Apple News and TTR
Apple News data lives in a separate repo (`rogerhirano-nw/apple-news`) — do
NOT pull it from inside this repo. The Apple Reporting API's TTR column is
not retrievable for publisher-side data; if anyone asks for TTR on Apple News
imports, point them to that repo's CLAUDE.md.

## Apple credentials (here, for the deal_health side)

The `deal_health/` and `confiant_*` paths use a mix of:
- `GAM_NETWORK_ID`, `GAM_SERVICE_ACCOUNT_JSON` (in GitHub secrets)
- `MAGNITE_KEY`, `MAGNITE_SECRET`, `MAGNITE_PUBLISHER_ID`
- `PUBMATIC_API_KEY`, `PUBMATIC_PUBLISHER_ID`
- `DATABASE_URL` (Postgres for some workflows that grew past SQLite)
- agentmail for outbound emails (see weekly_report.yml)

None of these touch tracked files. `.env`, `*.db`, `.streamlit/secrets.toml`
are gitignored. Verified clean: `git ls-files --error-unmatch .env` errors.

## Common operations

```bash
# fetch first (multi-laptop hazard)
git fetch && git status

# new work
git checkout -b feat/<short-description>
# ... edit ...
git add <specific files>     # NEVER `git add -A` — avoids dashboard.py
                              # WIP from the other laptop
git commit -m "..."
git push -u origin HEAD
gh pr create --fill          # opens PR against main

# ship a fix to Streamlit Cloud after merging to main
git checkout mac-studio
git merge main
git push

# run the dashboard locally
streamlit run dashboard.py

# run tests
python -m pytest tests/ -v
```

## Operational hazards

1. **`dashboard.py` is ~330KB single file.** Editing it from two laptops is
   the most common source of merge conflicts. Pull before editing.
2. **Magnite quirks** (full list in `README.md`): pull yesterday not today,
   500K row cap, 5 parallel reports max, datasets are siloed, 429 can lie.
3. **Confiant → GAM Protection sync is Playwright-driven** because GAM
   Protection has no API. If Google ships a UI change the sync breaks
   silently. Recovery flow in `docs/confiant_blocklist.md`.
4. **Streamlit Cloud secrets in `.streamlit/secrets.toml`** — never committed.
   Rotation requires updating both the local file and the Streamlit Cloud
   dashboard's Secrets section.
5. **Cron-job.org PAT** for triggering workflows from external schedule.
   Same shared-blast-radius caveat as the apple-news repo: anyone with the
   cron-job.org API key can read the PAT in cleartext.

## CI

`.github/workflows/ci.yml` runs pytest on every push to `main` and every PR
against `main`. No credentials needed — the test suite is hermetic.

`.github/dependabot.yml` opens weekly PRs to bump Actions versions. Important
ahead of the **Sept 2026 Node 20 removal** that would otherwise hard-fail
several of our 6 scheduled workflows.
