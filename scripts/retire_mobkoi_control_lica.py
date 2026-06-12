"""Retire (deactivate) line-item/creative associations — used to pull the
un-mirrored Mobkoi control creative out of rotation once the mirror A/B was
banked (docs/mobkoi_viewability.md).

For each `lineItemId:creativeId` pair: fetch the LICA, print its status,
and on APPLY=true run `DeactivateLineItemCreativeAssociations` scoped to
exactly that pair. Idempotent: already-inactive associations are skipped.
Fully reversible in the GAM UI (or via ActivateLineItemCreativeAssociations).

Env: RETIRE_PAIRS="li:creative,li:creative" (default: the Invesco control),
APPLY ("true" to write; default dry-run).
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

PAIRS = [
    tuple(p.split(":"))
    for p in (os.environ.get("RETIRE_PAIRS") or "7310815861:138557481462").split(",")
    if p.strip()
]
APPLY = (os.environ.get("APPLY") or "false").strip().lower() in ("1", "true", "yes")


def main() -> int:
    print(f"mode: {'APPLY' if APPLY else 'DRY RUN'}")
    print(f"pairs: {PAIRS}\n")

    gam = GAMClient()
    client = gam._get_soap_client()
    from googleads import ad_manager  # type: ignore
    svc = client.GetService(
        "LineItemCreativeAssociationService", version=gam._SOAP_API_VERSION
    )

    to_retire: list[tuple[str, str]] = []
    for li, cr in PAIRS:
        stmt = (ad_manager.StatementBuilder(version=gam._SOAP_API_VERSION)
                .Where("lineItemId = :li AND creativeId = :cr")
                .WithBindVariable("li", int(li))
                .WithBindVariable("cr", int(cr))
                .Limit(1))
        results = svc.getLineItemCreativeAssociationsByStatement(
            stmt.ToStatement()).results or []
        if not results:
            print(f"LICA {li}:{cr} — NOT FOUND, skipping")
            continue
        status = getattr(results[0], "status", None)
        print(f"LICA {li}:{cr} — status {status}")
        if str(status) != "ACTIVE":
            print("  not ACTIVE — skipping (idempotent)")
            continue
        to_retire.append((li, cr))

    print()
    if not to_retire:
        print("nothing to retire.")
        return 0
    if not APPLY:
        print(f"DRY RUN — would deactivate {len(to_retire)} association(s). "
              "Re-run with APPLY=true to write.")
        return 0

    for li, cr in to_retire:
        stmt = (ad_manager.StatementBuilder(version=gam._SOAP_API_VERSION)
                .Where("lineItemId = :li AND creativeId = :cr")
                .WithBindVariable("li", int(li))
                .WithBindVariable("cr", int(cr)))
        result = svc.performLineItemCreativeAssociationAction(
            {"xsi_type": "DeactivateLineItemCreativeAssociations"},
            stmt.ToStatement())
        n = int(getattr(result, "numChanges", 0) or 0)
        print(f"DEACTIVATED LICA {li}:{cr} — {n} change(s)")
        if n != 1:
            print("  !! expected exactly 1 change — check in the UI")
    print("done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
