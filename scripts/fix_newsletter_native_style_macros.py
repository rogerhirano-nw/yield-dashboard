"""
One-off: fix the macro syntax in the three newsletter native styles.

The styles were created with bare `[Logo]` / `[AdImage]` / `[ClickURL]` /
`[Headline]` / `[BodyCopy]` / `[CTAText]` tokens, but GAM only substitutes
creative-template variables written as `[%Var%]` (compare the in-prod ACTIVE
style 989975 "Insights Premium Spotlight", which uses `[%LOGO%]`). Left
as-is, the styles would render the literal bracket text once activated.

Per style this script:
  1. Pulls the style's native format (creative template) and reads each
     variable's `uniqueName` — macro casing must match it exactly.
  2. Rewrites bare `[Var]` tokens to `[%Var%]` in htmlSnippet/cssSnippet,
     matching tokens case-insensitively but emitting the format's exact
     uniqueName casing. Tokens that don't correspond to a format variable
     are never rewritten — they're surfaced for human review instead.
  3. Verifies every `[%...%]` macro in the result matches a format variable
     uniqueName (case-sensitive); any mismatch aborts before writing.
  4. Targets the style to the-bulletin ad unit (23330621742,
     includeDescendants) instead of run-of-network (`--skip-targeting`
     to leave targeting alone).
  5. With --apply, writes via NativeStyleService.updateNativeStyles, then
     re-fetches and re-verifies. Status is never touched — activation
     requires performNativeStyleAction, which this script never calls —
     so the styles stay INACTIVE until the newsletter campaign launches.

Dry-run by default; pass --apply to write. Runs from GitHub Actions via
.github/workflows/fix_newsletter_native_styles.yml (repo secrets), or
locally with GAM_SERVICE_ACCOUNT_JSON / GAM_NETWORK_ID in .env.

NOTE: the local-only setup scripts (scripts/setup_newsletter_native_styles.py
and the Bottom Banner block in scripts/setup_newsletter_campaign.py — not in
this repo) still contain the bare-token HTML. Update them from the final
snippets this run prints, or a local re-run will regress the live styles
(this script is idempotent — re-run it to re-fix).
"""

import argparse
import difflib
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path

API_VERSION = "v202605"
BULLETIN_AD_UNIT_ID = "23330621742"  # the-bulletin
REFERENCE_STYLE_ID = 989975  # Insights Premium Spotlight — known-good, read-only

# style id -> (name, expected creative template / "native format" id) per the
# audit. The live style's creativeTemplateId is the source of truth; these
# are a cross-check and the script warns if they disagree.
EXPECTED = {
    972438: ("Newsletter - Top Logo Style (600x80)", 12544544),
    972441: ("Newsletter - Bottom Banner Style (300x250)", 12543656),
    977473: ("Newsletter - Sponsored Content Style (600x314)", 12544547),
}

REPO_ROOT = Path(__file__).resolve().parent.parent
env_file = REPO_ROOT / ".env"
if env_file.exists():
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

# [%Var%] macro (the form GAM substitutes) and bare [Var] token (the bug).
MACRO = re.compile(r"\[%\s*([^%\]]*?)\s*%\]")
BARE_TOKEN = re.compile(r"\[(?!%)\s*([A-Za-z][A-Za-z0-9_-]*)\s*\]")


def soap_retry(call, what, attempts=3, base_sleep=15):
    """Same transient-ServerError retry as gam_client._soap_retry."""
    for attempt in range(1, attempts + 1):
        try:
            return call()
        except Exception as exc:
            if attempt == attempts or "SERVER_ERROR" not in str(exc):
                raise
            sleep_s = base_sleep * attempt
            print(f"  ! {what}: transient GAM ServerError "
                  f"(attempt {attempt}/{attempts}) — retrying in {sleep_s}s")
            time.sleep(sleep_s)


def get_client():
    from googleads import ad_manager, oauth2  # noqa: PLC0415

    key_data = json.loads(os.environ["GAM_SERVICE_ACCOUNT_JSON"])
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(key_data, f)
        key_file = f.name
    oauth2_client = oauth2.GoogleServiceAccountClient(
        key_file, "https://www.googleapis.com/auth/dfp"
    )
    return ad_manager.AdManagerClient(
        oauth2_client, "NewsweekDashboard/1.0",
        network_code=os.environ["GAM_NETWORK_ID"],
    )


def fetch_styles(client, ids):
    from googleads import ad_manager  # noqa: PLC0415

    svc = client.GetService("NativeStyleService", version=API_VERSION)
    sb = ad_manager.StatementBuilder(version=API_VERSION)
    sb.Where(f"id IN ({', '.join(str(i) for i in ids)})")
    resp = soap_retry(lambda: svc.getNativeStylesByStatement(sb.ToStatement()),
                      "getNativeStylesByStatement")
    return {s.id: s for s in (getattr(resp, "results", None) or [])}, svc


def fetch_template_vars(client, template_ids):
    from googleads import ad_manager  # noqa: PLC0415

    svc = client.GetService("CreativeTemplateService", version=API_VERSION)
    sb = ad_manager.StatementBuilder(version=API_VERSION)
    sb.Where(f"id IN ({', '.join(str(i) for i in template_ids)})")
    resp = soap_retry(lambda: svc.getCreativeTemplatesByStatement(sb.ToStatement()),
                      "getCreativeTemplatesByStatement")
    out = {}
    for t in (getattr(resp, "results", None) or []):
        out[t.id] = {
            "name": t.name,
            "variables": [
                {
                    "uniqueName": v.uniqueName,
                    "type": type(v).__name__,
                    "isRequired": getattr(v, "isRequired", None),
                }
                for v in (getattr(t, "variables", None) or [])
            ],
        }
    return out


def fix_snippet(snippet, unique_names):
    """Rewrite bare [Var] tokens to [%Var%] for each format variable.

    Token match is case-insensitive ([logo], [Logo], [LOGO] all count); the
    replacement always uses the format's exact uniqueName casing. `[%Var%]`
    occurrences are untouched — after `[` the regex requires the token, not
    `%`. Returns (new_snippet, human-readable replacement notes).
    """
    if not snippet:
        return snippet, []
    new, notes = snippet, []
    for un in unique_names:
        pat = re.compile(r"\[(?!%)\s*" + re.escape(un) + r"\s*\]", re.IGNORECASE)
        found = pat.findall(new)
        if found:
            new = pat.sub(f"[%{un}%]", new)
            variants = " / ".join(sorted(set(found)))
            notes.append(f"{variants} ×{len(found)} → [%{un}%]")
    return new, notes


def verify_snippet(snippet, unique_names, where):
    """Returns (errors, warnings) for a snippet against its format variables."""
    errors, warnings = [], []
    if not snippet:
        return errors, warnings
    names = set(unique_names)
    lower = {n.lower() for n in names}
    for m in MACRO.finditer(snippet):
        if m.group(1) not in names:
            errors.append(f"{where}: [%{m.group(1)}%] does not match any format "
                          f"variable uniqueName (case-sensitive) {sorted(names)}")
    for m in BARE_TOKEN.finditer(snippet):
        tok = m.group(1)
        if tok.lower() in lower:
            errors.append(f"{where}: bare token [{tok}] still present — "
                          f"should be [%...%]")
        else:
            warnings.append(f"{where}: leftover bracket token [{tok}] is not a "
                            f"format variable; probably literal text/CSS — "
                            f"verify manually")
    return errors, warnings


def describe_targeting(t):
    if t is None:
        return "none (run-of-network)"
    try:
        from zeep.helpers import serialize_object  # noqa: PLC0415
        d = {k: v for k, v in dict(serialize_object(t)).items() if v}
        return json.dumps(d, indent=2, default=str) if d else "none (run-of-network)"
    except Exception:
        return str(t)


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--apply", action="store_true",
                    help="write changes to GAM (default: dry-run)")
    ap.add_argument("--skip-targeting", action="store_true",
                    help="only fix macros; leave targeting untouched")
    args = ap.parse_args()

    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"=== Newsletter native style macro fix — {mode} ===\n")

    client = get_client()
    styles, style_svc = fetch_styles(client, [*EXPECTED, REFERENCE_STYLE_ID])

    missing = [sid for sid in EXPECTED if sid not in styles]
    if missing:
        print(f"FATAL: styles not found in GAM: {missing}")
        return 1

    template_ids = sorted({styles[sid].creativeTemplateId for sid in EXPECTED})
    templates = fetch_template_vars(client, template_ids)

    print("--- Native format (creative template) variable uniqueNames ---")
    for tid in template_ids:
        t = templates.get(tid)
        if not t:
            print(f"FATAL: creative template {tid} not found")
            return 1
        print(f"  {tid} \"{t['name']}\":")
        for v in t["variables"]:
            req = "required" if v["isRequired"] else "optional"
            print(f"    [%{v['uniqueName']}%]  ({v['type']}, {req})")
    print()

    to_update, all_errors, all_warnings = [], [], []

    for sid, (exp_name, exp_fmt) in EXPECTED.items():
        style = styles[sid]
        tid = style.creativeTemplateId
        print(f"--- Style {sid} \"{style.name}\" ---")
        print(f"  status: {style.status}   creativeTemplateId: {tid}")
        if tid != exp_fmt:
            print(f"  ! WARNING: audit expected format {exp_fmt}, live style "
                  f"says {tid} — using the live value")
        if style.status != "INACTIVE":
            print(f"  ! WARNING: expected INACTIVE, found {style.status} — "
                  f"status is left untouched either way")

        unique_names = [v["uniqueName"] for v in templates[tid]["variables"]]
        changed = False

        for field in ("htmlSnippet", "cssSnippet"):
            old = getattr(style, field, None)
            new, notes = fix_snippet(old, unique_names)
            errors, warnings = verify_snippet(new, unique_names,
                                              f"style {sid} {field}")
            all_errors += errors
            all_warnings += warnings
            if new != old:
                changed = True
                print(f"  {field}: {len(notes)} macro(s) to fix")
                for note in notes:
                    print(f"    {note}")
                for line in difflib.unified_diff(
                    (old or "").splitlines(), new.splitlines(),
                    fromfile=f"{field} (live)", tofile=f"{field} (fixed)",
                    lineterm="",
                ):
                    print(f"    {line}")
            else:
                print(f"  {field}: no macro changes needed")
            setattr(style, field, new)

        if not args.skip_targeting:
            print(f"  targeting (current): "
                  f"{describe_targeting(getattr(style, 'targeting', None))}")
            style.targeting = {
                "inventoryTargeting": {
                    "targetedAdUnits": [{
                        "adUnitId": BULLETIN_AD_UNIT_ID,
                        "includeDescendants": True,
                    }]
                }
            }
            print(f"  targeting (new): the-bulletin ad unit "
                  f"{BULLETIN_AD_UNIT_ID}, includeDescendants=true")
            changed = True

        if changed:
            to_update.append(style)
        print()

    ref = styles.get(REFERENCE_STYLE_ID)
    if ref is not None and not args.apply:
        print(f"--- Reference (read-only): style {REFERENCE_STYLE_ID} "
              f"\"{ref.name}\" status={ref.status} ---")
        print(f"  macros in use: "
              f"{sorted(set(MACRO.findall(ref.htmlSnippet or '')))}")
        print(f"  htmlSnippet:\n{ref.htmlSnippet}\n")

    if all_warnings:
        print("--- Warnings (manual review, not blocking) ---")
        for w in all_warnings:
            print(f"  ! {w}")
        print()
    if all_errors:
        print("--- ERRORS — refusing to write anything ---")
        for e in all_errors:
            print(f"  X {e}")
        return 1

    if not args.apply:
        print("--- Proposed final snippets "
              "(also what the local setup scripts should contain) ---")
        for sid in EXPECTED:
            style = styles[sid]
            print(f"\n### {sid} \"{style.name}\" htmlSnippet:\n{style.htmlSnippet}")
            if getattr(style, "cssSnippet", None):
                print(f"\n### {sid} cssSnippet:\n{style.cssSnippet}")
        print(f"\nDRY-RUN complete — would update {len(to_update)} style(s). "
              f"Re-run with --apply to write.")
        return 0

    if not to_update:
        print("Nothing to update — already clean.")
        return 0

    print(f"Updating {len(to_update)} native style(s) via "
          f"NativeStyleService.updateNativeStyles…")
    updated = soap_retry(lambda: style_svc.updateNativeStyles(to_update),
                         "updateNativeStyles")
    print(f"  updated ids: {[u.id for u in (updated or [])]}\n")

    print("--- Post-update verification (re-fetched from GAM) ---")
    fresh, _ = fetch_styles(client, list(EXPECTED))
    bad = False
    for sid in EXPECTED:
        style = fresh[sid]
        unique_names = [v["uniqueName"]
                        for v in templates[style.creativeTemplateId]["variables"]]
        errs, warns = [], []
        for field in ("htmlSnippet", "cssSnippet"):
            e, w = verify_snippet(getattr(style, field, None), unique_names,
                                  f"style {sid} {field}")
            errs += e
            warns += w
        ad_units = []
        try:
            ad_units = [str(u.adUnitId) for u in
                        style.targeting.inventoryTargeting.targetedAdUnits]
        except Exception:
            pass
        ok = (not errs and style.status == "INACTIVE"
              and (args.skip_targeting or BULLETIN_AD_UNIT_ID in ad_units))
        flag = "OK  " if ok else "FAIL"
        print(f"  [{flag}] {sid} \"{style.name}\" status={style.status} "
              f"targeted_ad_units={ad_units or 'none'}")
        for e in errs:
            print(f"      X {e}")
        for w in warns:
            print(f"      ! {w}")
        bad = bad or not ok

    print("\n--- Final live snippets "
          "(copy these into the local setup scripts) ---")
    for sid in EXPECTED:
        style = fresh[sid]
        print(f"\n### {sid} \"{style.name}\" htmlSnippet:\n{style.htmlSnippet}")
        if getattr(style, "cssSnippet", None):
            print(f"\n### {sid} cssSnippet:\n{style.cssSnippet}")

    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
