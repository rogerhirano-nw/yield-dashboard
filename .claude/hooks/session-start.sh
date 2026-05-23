#!/bin/bash
# Install the GitHub CLI in Claude Code on the Web sessions and
# authenticate it with $GH_TOKEN so Claude can run `gh workflow run`,
# `gh run view`, etc. directly from the container — no need to bounce
# through the user's laptop.
#
# Configure GH_TOKEN as an env var on the Claude Code on the Web
# environment (fine-grained PAT scoped to rogerhirano-nw/yield-dashboard
# with at minimum: actions:read+write, contents:read, metadata:read).

set -euo pipefail

# Only run inside the remote container — no-op locally.
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

# Idempotent install: skip the apt dance if gh is already present.
if ! command -v gh >/dev/null 2>&1; then
  echo "Installing GitHub CLI…"
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get install -y --no-install-recommends curl ca-certificates gnupg >/dev/null
  mkdir -p -m 755 /etc/apt/keyrings
  curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
    | tee /etc/apt/keyrings/githubcli-archive-keyring.gpg >/dev/null
  chmod go+r /etc/apt/keyrings/githubcli-archive-keyring.gpg
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
    | tee /etc/apt/sources.list.d/github-cli.list >/dev/null
  apt-get update -qq
  apt-get install -y --no-install-recommends gh >/dev/null
  echo "gh installed: $(gh --version | head -1)"
else
  echo "gh already installed: $(gh --version | head -1)"
fi

# Verify auth. gh auto-detects GH_TOKEN — no `gh auth login` needed.
if [ -z "${GH_TOKEN:-}" ]; then
  echo "WARNING: GH_TOKEN is not set. Configure it in the environment's"
  echo "variables UI (https://code.claude.com/docs/en/claude-code-on-the-web)"
  echo "with a fine-grained PAT scoped to rogerhirano-nw/yield-dashboard."
  exit 0
fi

if gh auth status >/dev/null 2>&1; then
  echo "gh authenticated as: $(gh api user --jq .login 2>/dev/null || echo unknown)"
else
  echo "WARNING: GH_TOKEN is set but gh auth status failed. Check the"
  echo "token's scopes and that it hasn't expired."
fi
