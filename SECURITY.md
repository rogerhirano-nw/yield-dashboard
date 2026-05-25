# Security Policy

## Repository visibility

This repository is **public** on GitHub. Public visibility is a deployment
requirement of Streamlit Community Cloud's free tier — the dashboard at
https://newsweek.streamlit.app deploys directly from the `mac-studio`
branch of this repo, and Streamlit's legacy OAuth integration only supports
public repositories. The `LICENSE` file makes clear that public visibility
is not a license grant.

Implications:

- All source code, configuration, commit messages, and issue/PR history
  are readable by anyone on the internet.
- Business logic — seller derivation rules, deal-source defaults,
  threshold rules, AE-code conventions — is exposed.
- **No credentials, database connection strings, or service-account JSON
  files are in the repository.** They live only in GitHub Actions Secrets
  (encrypted at rest, consumed by workflows by name) and in local `.env`
  files (gitignored, never committed). Verified clean by fingerprint
  searches across all of git history.

If the repository ever needs to become private, hosting must move off
Streamlit Community Cloud's free tier first — Streamlit's paid tier,
Render, Fly.io, or internal infrastructure all support private GitHub
repos.

## Reporting a vulnerability or credential leak

If you discover a security issue — a credential committed by mistake, an
exposed API key, an authentication bug, or anything that could let an
unauthorized party read or modify ad-ops data — **report it to the
repository owner directly** rather than opening a public issue.

Include:

- What you found (file path + commit, or screenshot).
- Whether you believe the secret was exposed publicly (forked, in CI logs,
  in a screenshot shared externally).
- Approximate time window of exposure.

## What we treat as a secret

- Anything in `.env` (`DATABASE_URL`, `GAM_*`, `MAGNITE_*`, `PUBMATIC_*`).
- `*.db` and `*.csv` cache files (may contain campaign-level revenue data).
- `.streamlit/secrets.toml`.
- GAM service-account JSON in any form.

All of the above are in `.gitignore`. **Do not commit them, even
temporarily.** If you need to share one for debugging, use a secure channel,
not git history.

## If a secret was committed

1. **Rotate the credential immediately** at the source (Supabase, GAM,
   Magnite, Pubmatic admin consoles). Rotation is mandatory even if the
   commit is later removed — git history is not a security boundary.
2. Update GitHub Actions secrets and local `.env` files on every laptop
   with the new value.
3. Remove the secret from history if practical (`git filter-repo` or BFG),
   force-push only after coordinating with the team — the deploy branch
   `mac-studio` will need to be reconciled.
4. Trigger `refresh.yml` to confirm jobs still authenticate.

## Out of scope

- Security of third-party services we depend on (Supabase, GAM REST API,
  Magnite, Pubmatic, Streamlit Cloud, GitHub Actions). Report those to the
  respective vendors.
