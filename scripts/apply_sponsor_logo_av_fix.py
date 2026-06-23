"""Apply the Active-View un-clip fix to the article sponsor-logo creative.

The sponsor-logo CustomCreative (default 138562255517, on Infiniti Newsmakers
LI 7336465381) injects a "Presented by <logo>" strip into the article
breadcrumb and glues the out-of-page carrier slot onto it so GAM Active View
measures the real position. The carrier was clipped to the logo's ~24px while
GAM forces the OOP iframe to ~150px, leaving only ~16% of the measured iframe
in view -- under AV's 50% bar -- so the LI booked ~0% viewable. This rewrites
the creative's htmlSnippet to docs/snippets/article_sponsor_logo_creative.html,
which sizes the carrier to the iframe's real height (no overflow clip). The
logo render path is unchanged; only the invisible carrier geometry moves.
(Root cause verified live 2026-06-22: IntersectionObserver in-view ratio 0.16
clipped vs 1.00 un-clipped on the JT Batson article; see docs/article_sponsor_logo.md.)

Dry-run by default -- prints a before/after summary. APPLY=true (env) performs
`updateCreatives`. Idempotent: a creative already carrying the new snippet is
skipped.

Env: CREATIVE_IDS (comma-separated, default 138562255517), APPLY.
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
    s.strip() for s in (os.environ.get("CREATIVE_IDS") or "138562255517").split(",")
    if s.strip()
]
APPLY = (os.environ.get("APPLY") or "false").strip().lower() in ("1", "true", "yes")

SNIPPET_FILE = REPO_ROOT / "docs" / "snippets" / "article_sponsor_logo_creative.html"
# Guard: only touch the sponsor-logo creative, identified by its watcher id.
SENTINEL = "nw-sponsor-logo-boot"


def main() -> int:
    new_snippet = SNIPPET_FILE.read_text()
    if SENTINEL not in new_snippet:
        print(f"!! {SNIPPET_FILE.name} is missing {SENTINEL!r} sentinel — aborting")
        return 2
    print(f"mode: {'APPLY' if APPLY else 'DRY RUN'}")
    print(f"creatives: {CREATIVE_IDS}")
    print(f"new snippet: {len(new_snippet)} chars from {SNIPPET_FILE.name}\n")

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
        cur = getattr(c, "htmlSnippet", None) or ""
        print(f"creative {cid}  [{ctype}]  {name[:70]}")

        if ctype != "CustomCreative":
            print("  !! not a CustomCreative — skipping")
            continue
        if SENTINEL not in cur:
            print(f"  !! live snippet has no {SENTINEL!r} — not the sponsor logo; skipping")
            continue
        if cur == new_snippet:
            print("  already carries the current snippet — skipping (idempotent)")
            continue

        clipped = "overflow:hidden" in cur and "height:100%;border" in cur
        print(f"  live snippet: {len(cur)} chars"
              + (" (has the old clipped carrier)" if clipped else ""))
        print(f"  new snippet:  {len(new_snippet)} chars")
        c.htmlSnippet = new_snippet
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
        print(f"UPDATED creative {u.id}  {u.name[:60]}  "
              f"(htmlSnippet now {len(u.htmlSnippet)} chars)")
    print("done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
