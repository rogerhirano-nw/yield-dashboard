"""Apply the iframe mirror to Mobkoi creatives (PR #164, doc section 1c).

For each creative id: fetch via SOAP CreativeService, verify it's the
expected Mobkoi ThirdPartyCreative, then rewrite the snippet as
  <vendor block, byte-identical through `<!-- END TAG -->`>
  + the NW iframe mirror block from docs/snippets/mobkoi_iframe_mirror_creative.html
dropping anything previously appended after the vendor block (the dead
declared-view watcher on 138562143597). Idempotent: creatives already
carrying the mirror are skipped.

Dry-run by default — prints the before/after snippet. APPLY=true (env)
performs `updateCreatives`.

Env: CREATIVE_IDS (comma-separated, default 138562143597), APPLY.
"""

from __future__ import annotations

import os
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

_env = REPO_ROOT / ".env"
if _env.exists():
    for _line in _env.read_text().splitlines():
        _line = _line.strip()
        if not _line or _line.startswith("#") or "=" not in _line:
            continue
        _k, _v = _line.split("=", 1)
        os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

from gam_client import GAMClient  # noqa: E402

CREATIVE_IDS = [
    s.strip() for s in (os.environ.get("CREATIVE_IDS") or "138562143597").split(",")
    if s.strip()
]
APPLY = (os.environ.get("APPLY") or "false").strip().lower() in ("1", "true", "yes")

END_MARKER = "<!-- END TAG -->"
MIRROR_MARKER = "<!-- NW iframe mirror"
SNIPPET_FILE = REPO_ROOT / "docs" / "snippets" / "mobkoi_iframe_mirror_creative.html"


def mirror_block() -> str:
    text = SNIPPET_FILE.read_text()
    i = text.find(MIRROR_MARKER)
    if i < 0:
        raise RuntimeError(f"mirror block marker not found in {SNIPPET_FILE}")
    return text[i:].strip()


def main() -> int:
    block = mirror_block()
    print(f"mode: {'APPLY' if APPLY else 'DRY RUN'}")
    print(f"creatives: {CREATIVE_IDS}")
    print(f"mirror block: {len(block)} chars from {SNIPPET_FILE.name}\n")

    gam = GAMClient()
    client = gam._get_soap_client()
    from googleads import ad_manager  # type: ignore
    svc = client.GetService("CreativeService", version=gam._SOAP_API_VERSION)

    ids = ", ".join(str(int(c)) for c in CREATIVE_IDS)
    stmt = ad_manager.StatementBuilder(version=gam._SOAP_API_VERSION) \
        .Where(f"id IN ({ids})").Limit(50)
    creatives = svc.getCreativesByStatement(stmt.ToStatement()).results or []
    found = {str(c.id): c for c in creatives}

    to_update = []
    for cid in CREATIVE_IDS:
        c = found.get(cid)
        print("=" * 72)
        if c is None:
            print(f"creative {cid}: NOT FOUND — skipping")
            continue
        name = getattr(c, "name", "")
        ctype = type(c).__name__
        snippet = getattr(c, "snippet", None) or ""
        print(f"creative {cid}  [{ctype}]  {name}")

        if ctype != "ThirdPartyCreative":
            print("  !! not a ThirdPartyCreative — skipping")
            continue
        if "tagservice.maximus.mobkoi.com" not in snippet:
            print("  !! snippet has no Mobkoi boot tag — skipping")
            continue
        if END_MARKER not in snippet:
            print(f"  !! snippet has no {END_MARKER!r} marker — skipping")
            continue
        if MIRROR_MARKER in snippet:
            print("  already carries the iframe mirror — skipping (idempotent)")
            continue

        vendor = snippet[: snippet.index(END_MARKER) + len(END_MARKER)]
        dropped = snippet[snippet.index(END_MARKER) + len(END_MARKER):].strip()
        new_snippet = vendor.rstrip() + "\n\n" + block + "\n"

        print(f"  vendor block: {len(vendor)} chars (kept byte-identical)")
        if dropped:
            print(f"  dropping {len(dropped)} chars previously appended "
                  f"(starts: {dropped[:60]!r})")
        print(f"  new snippet: {len(new_snippet)} chars")
        print("  --- new snippet ---")
        print(new_snippet)
        c.snippet = new_snippet
        to_update.append(c)

    print("=" * 72)
    if not to_update:
        print("nothing to update.")
        return 0
    if not APPLY:
        print(f"DRY RUN — would update {len(to_update)} creative(s). "
              "Re-run with APPLY=true to write.")
        return 0

    updated = svc.updateCreatives(to_update)
    for u in updated:
        print(f"UPDATED creative {u.id}  {u.name}  "
              f"(snippet now {len(u.snippet)} chars)")
    print("done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
