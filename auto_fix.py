"""LLM-in-the-loop auto-fix for the Confiant blocklist daily cron.

When the cron hits a hard failure (after the URL-fallback chain + retries
exhaust), this module calls the Anthropic API with the failure context and
proposes a fix as a GitHub draft PR. The cron itself never auto-merges
the proposed fix — a human reviews the diff and decides. The proposal is
just informed by what the LLM thinks happened.

Architecture deliberately bounded:

  - **No auto-merge.** Always a draft PR.
  - **Single allowlisted file** that the LLM can propose changes to (per
    failure context). For the URL-migration case, that's
    `gam_blocklist_ui.py`; for selector breakage, the same. The list is
    explicit in EDITABLE_FILES.
  - **24h cooldown** via ~/.confiant-blocklist/auto_fix_last.json so a
    misdiagnosis doesn't open 100 PRs over a stuck cron.
  - **Full audit trail** — the LLM prompt + raw response get embedded in
    the PR body verbatim. Anyone reviewing the PR sees exactly what the
    LLM was told and what it returned.
  - **Cost cap** — single Claude call per invocation, sonnet, ~8k token
    output max. Roughly $0.05-$0.30 per invocation.

For the legal-disclosure piece (production runtime calls Anthropic with
prod data): see project_newsweek_legal_claude_audit_2026_06.md in
memory and docs/confiant_blocklist.md "Auto-fix MVP" section.

CLI for manual testing:
    python auto_fix.py --replay-from-state-runs <run_id>

…replays a failed run from state.sqlite (or supply a manual context file
with --context-file).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path

# Files the LLM is allowed to propose changes to. Anything else returns
# a refusal-style fix proposal that the human can act on manually.
EDITABLE_FILES = {
    "gam_blocklist_ui.py",
    "gam_arc.py",
    "confiant_client.py",
    "scripts/confiant_gam_arc_block.py",
}

COOLDOWN_PATH = Path("~/.confiant-blocklist/auto_fix_last.json").expanduser()
COOLDOWN_SECONDS = 24 * 60 * 60  # 24h

ANTHROPIC_MODEL = "claude-sonnet-4-6"
MAX_OUTPUT_TOKENS = 8000


@dataclass
class FixProposal:
    """What the LLM returned. Persisted verbatim into the PR body for
    audit-trail purposes."""
    diagnosis: str
    proposed_file: str
    proposed_diff: str  # unified-diff style; NOT applied without human review
    confidence_label: str  # "high" / "medium" / "low" — LLM's own self-rating
    refusal_reason: str | None = None  # set when the LLM declined to propose
    prompt_used: str | None = None     # full prompt sent (for audit)
    raw_response: str | None = None    # raw model output (for audit)


def _within_cooldown() -> tuple[bool, float]:
    """Returns (is_within_cooldown, seconds_until_next_attempt)."""
    if not COOLDOWN_PATH.exists():
        return False, 0
    try:
        last = json.loads(COOLDOWN_PATH.read_text())
        last_ts = last.get("ts", 0)
        elapsed = time.time() - last_ts
        if elapsed < COOLDOWN_SECONDS:
            return True, COOLDOWN_SECONDS - elapsed
    except Exception:
        pass
    return False, 0


def _record_attempt(pr_url: str | None, refused: bool) -> None:
    COOLDOWN_PATH.parent.mkdir(parents=True, exist_ok=True)
    COOLDOWN_PATH.write_text(json.dumps({
        "ts": time.time(),
        "iso": datetime.now(timezone.utc).isoformat(),
        "pr_url": pr_url,
        "refused": refused,
    }))


def _read_file_excerpt(path: Path, around_line: int | None = None,
                       max_lines: int = 250) -> str:
    """Read the file. If around_line is given, return a window centered on it;
    otherwise return the whole file (capped). The model sees enough context to
    propose a fix without being drowned in unrelated code."""
    if not path.exists():
        return f"(file not found: {path})"
    text = path.read_text()
    lines = text.splitlines()
    if around_line is None or len(lines) <= max_lines:
        return text[:50000]  # generous bound; sonnet has plenty of context
    half = max_lines // 2
    start = max(0, around_line - half)
    end = min(len(lines), around_line + half)
    return f"# (excerpt: lines {start+1}-{end} of {len(lines)})\n" + \
           "\n".join(lines[start:end])


def _recent_commits_touching(path: Path, n: int = 5) -> str:
    try:
        out = subprocess.check_output(
            ["git", "log", "--oneline", "-n", str(n), "--", str(path)],
            cwd=str(path.parent), stderr=subprocess.DEVNULL, text=True,
        )
        return out.strip() or "(no recent commits)"
    except Exception:
        return "(git log unavailable)"


def _build_prompt(
    error_traceback: str,
    failing_file: Path,
    around_line: int | None,
    screenshot_b64: str | None,
    extra_context: dict | None,
) -> str:
    excerpt = _read_file_excerpt(failing_file, around_line=around_line)
    history = _recent_commits_touching(failing_file)
    extra = ""
    if extra_context:
        extra = "\n\nADDITIONAL CONTEXT:\n" + json.dumps(extra_context, indent=2)

    return f"""You are a code review assistant helping diagnose an automated cron failure
in a production Confiant -> GAM Protection blocklist pipeline. The cron failed
during its 04:00 ET run. A human will REVIEW your proposed fix as a draft PR
— you are NOT applying anything directly.

Be skeptical. If the failure is ambiguous (could be network blip, could be a
real change in GAM's UI), say so and recommend the human investigate. Do NOT
silently rewrite logic to make the error disappear — for example, do not
remove a sanity-check just because it raised.

YOUR OUTPUT MUST BE VALID JSON matching this schema exactly:

{{
  "diagnosis": "<one paragraph explaining what likely broke and why>",
  "confidence_label": "high|medium|low",
  "proposed_file": "<relative path of the single file to change>",
  "proposed_diff": "<unified-diff style content showing exactly what to change. Use === BEFORE === and === AFTER === fenced blocks if a full unified diff is awkward, but prefer unified diff. KEEP IT MINIMAL — one targeted change. Empty string if you refuse>",
  "refusal_reason": "<set when proposed_diff is empty; explain why a code change isn't the right answer>"
}}

Refuse if any of these apply:
  - The fix would require modifying a file outside this list: {sorted(EDITABLE_FILES)}
  - The failure is a Google login/session-expiry case (needs human re-auth)
  - The failure is a Confiant API outage (transient; will self-resolve)
  - You can't identify a concrete change with high confidence

FAILURE CONTEXT:
================
Failing file: {failing_file}
Around line: {around_line}

Error traceback:
{error_traceback}

Recent commits touching the failing file:
{history}

Current contents of the failing file (or relevant excerpt):
```
{excerpt}
```
{extra}

Return ONLY the JSON object. No prose around it.
"""


def analyze_failure(
    error_traceback: str,
    failing_file: Path,
    around_line: int | None = None,
    screenshot_path: Path | None = None,
    extra_context: dict | None = None,
) -> FixProposal:
    """Call Anthropic to diagnose + propose a fix. Returns a FixProposal,
    never raises (errors are captured into refusal_reason)."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return FixProposal(
            diagnosis="ANTHROPIC_API_KEY not set — auto-fix skipped",
            proposed_file="", proposed_diff="", confidence_label="low",
            refusal_reason="No ANTHROPIC_API_KEY available; failure email "
                           "is the only signal you'll get.",
        )

    prompt = _build_prompt(error_traceback, failing_file, around_line,
                           screenshot_b64=None, extra_context=extra_context)
    try:
        import anthropic
    except ImportError:
        return FixProposal(
            diagnosis="anthropic SDK not installed — auto-fix skipped",
            proposed_file="", proposed_diff="", confidence_label="low",
            refusal_reason="pip install anthropic and retry.",
        )

    client = anthropic.Anthropic(api_key=api_key)
    try:
        msg = client.messages.create(
            model=ANTHROPIC_MODEL,
            max_tokens=MAX_OUTPUT_TOKENS,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = "".join(b.text for b in msg.content if hasattr(b, "text"))
    except Exception as e:
        return FixProposal(
            diagnosis=f"Anthropic API call failed: {e}",
            proposed_file="", proposed_diff="", confidence_label="low",
            refusal_reason=str(e),
            prompt_used=prompt,
        )

    # Extract JSON from the response. The model might wrap it in a code fence
    # despite our instructions; tolerate both.
    json_text = raw.strip()
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", json_text, re.DOTALL)
    if m:
        json_text = m.group(1)
    try:
        parsed = json.loads(json_text)
    except json.JSONDecodeError as e:
        return FixProposal(
            diagnosis=f"Model response wasn't valid JSON: {e}",
            proposed_file="", proposed_diff="", confidence_label="low",
            refusal_reason=raw[:1500],
            prompt_used=prompt,
            raw_response=raw,
        )

    proposed_file = parsed.get("proposed_file", "").strip()
    proposed_diff = parsed.get("proposed_diff", "").strip()

    # Validate the proposed file is on the allowlist
    if proposed_file and proposed_file not in EDITABLE_FILES:
        return FixProposal(
            diagnosis=parsed.get("diagnosis", ""),
            proposed_file=proposed_file,
            proposed_diff="",
            confidence_label=parsed.get("confidence_label", "low"),
            refusal_reason=(
                f"Proposed change to {proposed_file!r} which isn't on the "
                f"EDITABLE_FILES allowlist {sorted(EDITABLE_FILES)}. Human "
                f"intervention required."
            ),
            prompt_used=prompt,
            raw_response=raw,
        )

    return FixProposal(
        diagnosis=parsed.get("diagnosis", ""),
        proposed_file=proposed_file,
        proposed_diff=proposed_diff,
        confidence_label=parsed.get("confidence_label", "medium"),
        refusal_reason=parsed.get("refusal_reason") or None,
        prompt_used=prompt,
        raw_response=raw,
    )


def open_draft_pr(proposal: FixProposal, repo_root: Path) -> str | None:
    """Open a draft GitHub PR with the proposal. Returns the PR URL on
    success, None if refused / no diff / gh CLI failure.

    The PR is ALWAYS draft. Branch name is timestamped to avoid collisions.
    The PR body includes the full LLM prompt + response for audit trail —
    a reviewer can see exactly what the LLM saw and decided.
    """
    if not proposal.proposed_diff or proposal.refusal_reason:
        return None

    branch = f"auto-fix/{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
    try:
        # Branch off main; never modify the user's working branch
        subprocess.run(["git", "fetch", "origin", "main"],
                       cwd=str(repo_root), check=False)
        subprocess.run(["git", "checkout", "-b", branch, "origin/main"],
                       cwd=str(repo_root), check=True)
    except subprocess.CalledProcessError as e:
        print(f"git branch creation failed: {e}", file=sys.stderr)
        return None

    # Apply diff — try `patch` first (handles unified diffs); fall back to
    # === BEFORE / AFTER === blocks if the model used that format.
    diff_path = repo_root / f".auto_fix_proposal_{branch.replace('/', '_')}.diff"
    diff_path.write_text(proposal.proposed_diff)
    try:
        # `git apply` is strict but doesn't need the file to be a real patch.
        ga = subprocess.run(
            ["git", "apply", "--whitespace=fix", str(diff_path)],
            cwd=str(repo_root), capture_output=True, text=True,
        )
        if ga.returncode != 0:
            # Try BEFORE/AFTER fallback
            ok = _apply_before_after_blocks(proposal, repo_root)
            if not ok:
                print(f"Patch did not apply cleanly: {ga.stderr[:500]}",
                      file=sys.stderr)
                subprocess.run(["git", "checkout", "main"], cwd=str(repo_root),
                               check=False)
                subprocess.run(["git", "branch", "-D", branch],
                               cwd=str(repo_root), check=False)
                return None
    finally:
        try: diff_path.unlink()
        except Exception: pass

    # Commit + push + draft PR
    subprocess.run(["git", "add", "-A"], cwd=str(repo_root), check=True)
    commit_msg = (
        f"auto-fix proposal: {proposal.diagnosis[:60].rstrip()}\n\n"
        f"LLM-proposed fix from auto_fix.py. Confidence: "
        f"{proposal.confidence_label}. **REVIEW BEFORE MERGING.** Full "
        f"prompt + raw response are in the PR body."
    )
    subprocess.run(["git", "commit", "-m", commit_msg],
                   cwd=str(repo_root), check=True)
    subprocess.run(["git", "push", "-u", "origin", branch],
                   cwd=str(repo_root), check=True)

    body = (
        f"## Auto-fix proposal — DRAFT, do not merge without review\n\n"
        f"### Diagnosis\n{proposal.diagnosis}\n\n"
        f"### Model confidence\n{proposal.confidence_label}\n\n"
        f"### File changed\n`{proposal.proposed_file}`\n\n"
        f"### What to check\n"
        f"- Does the diagnosis match what you'd say after looking at the error?\n"
        f"- Is the diff minimal and surgical? Reject if it touches unrelated "
        f"code.\n"
        f"- Does the change preserve sanity-checks (don't silently allow "
        f"a previously-validated condition through)?\n\n"
        f"### Full LLM prompt (audit trail)\n<details>\n<summary>click to expand</summary>\n\n"
        f"```\n{(proposal.prompt_used or '')[:8000]}\n```\n</details>\n\n"
        f"### Raw LLM response\n<details>\n<summary>click to expand</summary>\n\n"
        f"```\n{(proposal.raw_response or '')[:8000]}\n```\n</details>\n"
    )
    try:
        pr_out = subprocess.check_output(
            ["gh", "pr", "create", "--draft",
             "--base", "main", "--head", branch,
             "--title", f"auto-fix: {proposal.diagnosis[:55]}",
             "--body", body],
            cwd=str(repo_root), text=True,
        )
        pr_url = pr_out.strip().splitlines()[-1]
        return pr_url
    except subprocess.CalledProcessError as e:
        print(f"gh pr create failed: {e}", file=sys.stderr)
        return None


def _apply_before_after_blocks(proposal: FixProposal, repo_root: Path) -> bool:
    """Fallback for when the LLM returned === BEFORE === / === AFTER === blocks
    instead of a true unified diff."""
    pat = re.compile(
        r"===\s*BEFORE\s*===\s*(.*?)===\s*AFTER\s*===\s*(.*?)(?====|\Z)",
        re.DOTALL,
    )
    blocks = list(pat.finditer(proposal.proposed_diff))
    if not blocks:
        return False
    target = repo_root / proposal.proposed_file
    if not target.exists():
        return False
    content = target.read_text()
    new = content
    for m in blocks:
        before = m.group(1).strip("\n")
        after = m.group(2).strip("\n")
        if before not in new:
            return False
        new = new.replace(before, after, 1)
    target.write_text(new)
    return True


def run(
    error_traceback: str,
    failing_file: Path,
    around_line: int | None = None,
    extra_context: dict | None = None,
    repo_root: Path | None = None,
) -> tuple[FixProposal, str | None]:
    """High-level entry point for the cron path:
        - cooldown check
        - LLM call
        - PR open
        - record attempt
    Returns (proposal, pr_url_or_None)."""
    in_cooldown, secs_left = _within_cooldown()
    if in_cooldown:
        return (FixProposal(
            diagnosis="Skipped — within 24h cooldown",
            proposed_file="", proposed_diff="", confidence_label="low",
            refusal_reason=f"Last auto-fix attempt was {COOLDOWN_SECONDS - secs_left:.0f}s "
                           f"ago; cooldown until {datetime.fromtimestamp(time.time() + secs_left)}.",
        ), None)

    proposal = analyze_failure(
        error_traceback, failing_file, around_line=around_line,
        extra_context=extra_context,
    )
    if not proposal.proposed_diff:
        _record_attempt(pr_url=None, refused=True)
        return proposal, None

    repo_root = repo_root or Path(__file__).parent
    pr_url = open_draft_pr(proposal, repo_root)
    _record_attempt(pr_url=pr_url, refused=False)
    return proposal, pr_url


# ── CLI for manual testing ────────────────────────────────────────────────

def _cli() -> int:
    # Same dotenv loader the rest of the pipeline uses
    try:
        from confiant_blocklist import _load_dotenv
        _load_dotenv()
    except Exception:
        pass

    p = argparse.ArgumentParser(description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--context-file", type=Path,
                   help="JSON file with keys: error_traceback, failing_file, "
                        "around_line (optional), extra_context (optional).")
    p.add_argument("--replay-from-state-runs", type=int, metavar="RUN_ID",
                   help="Replay the failure recorded in state.sqlite for this "
                        "run_id. Convenient for testing on a real past failure.")
    p.add_argument("--dry-run", action="store_true",
                   help="Print proposal but don't push or open PR.")
    args = p.parse_args()

    if not (args.context_file or args.replay_from_state_runs):
        p.error("provide --context-file or --replay-from-state-runs")

    if args.replay_from_state_runs:
        import sqlite3
        state = Path("~/.confiant-blocklist/state.sqlite").expanduser()
        with sqlite3.connect(state) as conn:
            row = conn.execute(
                "SELECT error FROM runs WHERE run_id = ?",
                (args.replay_from_state_runs,),
            ).fetchone()
        if not row or not row[0]:
            print(f"Run {args.replay_from_state_runs} has no recorded error.",
                  file=sys.stderr)
            return 2
        ctx = {
            "error_traceback": row[0],
            "failing_file": "gam_blocklist_ui.py",
        }
    else:
        ctx = json.loads(args.context_file.read_text())

    if args.dry_run:
        prop = analyze_failure(
            error_traceback=ctx["error_traceback"],
            failing_file=Path(__file__).parent / ctx["failing_file"],
            around_line=ctx.get("around_line"),
            extra_context=ctx.get("extra_context"),
        )
        print(json.dumps(asdict(prop), indent=2))
        return 0

    prop, pr_url = run(
        error_traceback=ctx["error_traceback"],
        failing_file=Path(__file__).parent / ctx["failing_file"],
        around_line=ctx.get("around_line"),
        extra_context=ctx.get("extra_context"),
        repo_root=Path(__file__).parent,
    )
    print(f"Diagnosis: {prop.diagnosis}")
    print(f"Confidence: {prop.confidence_label}")
    if prop.refusal_reason:
        print(f"Refused: {prop.refusal_reason}")
    if pr_url:
        print(f"Draft PR: {pr_url}")
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
