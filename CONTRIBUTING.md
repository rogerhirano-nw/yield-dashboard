# Contributing

This repo powers the Newsweek ad-ops yield dashboard. See [README.md](README.md)
for a project overview and [CLAUDE.md](CLAUDE.md) for code conventions used by
Claude Code sessions.

## Branches

- **`main`** — source of truth. All PRs target `main`.
- **`mac-studio`** — the branch Streamlit Cloud deploys from. Updated by
  merging `main` → `mac-studio` after a fix lands. Until that merge happens,
  the fix is not live.
- **Feature / fix branches** — `fix/...`, `feat/...`, `chore/...`,
  `diagnose/...`. Short, descriptive, and deleted after merge.

## Pull requests

- Open every change as a PR — even small fixes. Direct pushes to `main` are
  reserved for time-sensitive diagnostic workflows prefixed `tmp_`.
- Use the PR template in `.github/pull_request_template.md`. Keep the title
  under 70 characters; details belong in the body.
- Squash-merge with `--delete-branch`. The commit history on `main` is one
  commit per merged PR.

## Multi-laptop workflow

The repo is cloned on multiple machines that all push as independent
collaborators.

- **Always `git fetch` before starting work.** `origin/main` may have moved
  since your last session.
- **Re-check `origin/main` before opening a PR** and rebase / merge if it
  moved during your session.
- Per-repo git identity only (`git config user.email ...` inside this clone),
  not global, to avoid mixing personal and work commits.

## Diagnostics

For one-off investigations that need GAM / Magnite / Pubmatic credentials,
write a `.github/workflows/tmp_<name>.yml` workflow with
`on: workflow_dispatch` (see existing `tmp_*.yml` files for the pattern).
Trigger via `gh workflow run`, capture results from the run log, and **delete
the workflow as part of the PR that fixes the underlying issue.**

## Secrets

Never commit credentials. The following are required in `.env` locally and
in GitHub Actions secrets:

- `DATABASE_URL` (Supabase Postgres connection string)
- `GAM_NETWORK_ID`, `GAM_SERVICE_ACCOUNT_JSON`
- `MAGNITE_KEY`, `MAGNITE_SECRET`, `MAGNITE_PUBLISHER_ID`
- `PUBMATIC_*`

If you suspect a credential has leaked, see [SECURITY.md](SECURITY.md).

## Refresh schedule

`refresh.yml` runs daily at 09:00 UTC and pulls yesterday's data into the
Supabase cache (which holds the last 7 days, not lifetime).
`weekly_report.yml` runs Mondays at 13:00 UTC. Both can be triggered manually
via `gh workflow run`.
