"""Start GitHub Actions workflows from the dashboard and fetch what they made.

The dashboard's Tools tab (final reports, screenshot decks) runs the same
workflows people used to dispatch by hand. It authenticates with
``GITHUB_DISPATCH_TOKEN`` — on Azure App Service that app setting is a Key
Vault reference to ``github-dispatch-token`` (the scheduler's token: Actions
read & write on the ad ops repos). ``GITHUB_REPO`` defaults to this repo.

Nothing here writes to GAM; the workflows it starts are read-only pulls.
"""
from __future__ import annotations

import io
import os
import zipfile

import requests

API = "https://api.github.com"
DEFAULT_REPO = "rogerhirano-nw/yield-dashboard"


def repo() -> str:
    return os.environ.get("GITHUB_REPO", DEFAULT_REPO)


def configured() -> bool:
    return bool(os.environ.get("GITHUB_DISPATCH_TOKEN"))


def _headers() -> dict:
    return {"Authorization": f"Bearer {os.environ['GITHUB_DISPATCH_TOKEN']}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "newsweek-adops-dashboard"}


def dispatch(workflow: str, inputs: dict, ref: str = "main") -> None:
    """Start ``workflow`` (file name) with ``inputs``. Blank inputs are dropped,
    booleans become the strings GitHub expects. Raises on any non-2xx."""
    clean = {k: (str(v).lower() if isinstance(v, bool) else str(v).strip())
             for k, v in inputs.items() if v not in (None, "")}
    r = requests.post(f"{API}/repos/{repo()}/actions/workflows/{workflow}/dispatches",
                      headers=_headers(), json={"ref": ref, "inputs": clean}, timeout=20)
    if r.status_code >= 300:
        raise RuntimeError(f"GitHub said {r.status_code}: {r.text[:300]}")


def recent_runs(workflow: str, n: int = 8) -> list[dict]:
    """Newest runs of ``workflow`` as plain dicts: id, title, status (queued /
    in_progress / completed), conclusion, created_at, url."""
    r = requests.get(f"{API}/repos/{repo()}/actions/workflows/{workflow}/runs",
                     headers=_headers(), params={"per_page": n}, timeout=20)
    r.raise_for_status()
    return [{"id": w["id"], "title": w.get("display_title") or w.get("name", ""),
             "status": w["status"], "conclusion": w.get("conclusion"),
             "created_at": w["created_at"], "url": w["html_url"]}
            for w in r.json().get("workflow_runs", [])]


def run_artifacts(run_id: int) -> list[dict]:
    r = requests.get(f"{API}/repos/{repo()}/actions/runs/{run_id}/artifacts",
                     headers=_headers(), timeout=20)
    r.raise_for_status()
    return [{"id": a["id"], "name": a["name"], "size": a["size_in_bytes"],
             "expired": a.get("expired", False)} for a in r.json().get("artifacts", [])]


def artifact_files(artifact_id: int) -> dict[str, bytes]:
    """Download one artifact (GitHub serves it as a zip) → {path: bytes}."""
    r = requests.get(f"{API}/repos/{repo()}/actions/artifacts/{artifact_id}/zip",
                     headers=_headers(), timeout=120)
    r.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        return {n: z.read(n) for n in z.namelist() if not n.endswith("/")}


def run_label(run: dict) -> str:
    """'Running', 'Queued', 'Done', 'Failed' — what a person reads in the list."""
    if run["status"] != "completed":
        return "Running" if run["status"] == "in_progress" else "Queued"
    return {"success": "Done", "cancelled": "Cancelled"}.get(run.get("conclusion"), "Failed")
