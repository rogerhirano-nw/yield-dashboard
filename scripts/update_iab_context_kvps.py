"""Sync a GAM custom targeting key with an IAB Content Taxonomy TSV.

Defaults to key 'iab_context_v3' + Content Taxonomy 3.1. The v2.2 taxonomy
goes to key id 19649704:
    python scripts/update_iab_context_kvps.py --tsv data/iab_content_taxonomy_2_2.tsv --key-id 19649704

Each taxonomy row becomes one value on the key:
    name        = the row's Unique ID   (e.g. "483", "JLBCU7", "v9i3On")
    displayName = the row's Name        (e.g. "Sports", "Entertainment")

Usage:
    python scripts/update_iab_context_kvps.py --dry-run
    python scripts/update_iab_context_kvps.py            # create/fix values

Lookup-first and safe to re-run: values already on the key are left alone
unless their displayName differs from the sheet's Name, in which case it is
updated. If the key itself doesn't exist it is created (PREDEFINED) — in
dry-run it is only reported.

Reuses the SOAP helpers from update_bmb_kvps.py (same service account path).
"""

import argparse
import csv
import logging
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from update_bmb_kvps import BATCH_SIZE, fetch_existing_values, get_soap_client  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)

DEFAULT_TSV = "data/iab_content_taxonomy_3_1.tsv"
DEFAULT_KEY = "iab_context_v3"
KEY_DISPLAY_NAME = "IAB Content Taxonomy v3.1"


def load_taxonomy(path: str) -> list[tuple[str, str]]:
    """Return [(unique_id, name)] from the IAB TSV (two header rows)."""
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f, delimiter="\t"))
    header = [c.strip() for c in rows[1]]
    if header[:3] != ["Unique ID", "Parent", "Name"]:
        sys.exit(f"Unexpected header row in {path}: {header[:3]}")
    out: list[tuple[str, str]] = []
    for r in rows[2:]:
        if not r or not r[0].strip():
            continue
        out.append((r[0].strip(), r[2].strip()[:255]))
    ids = [i for i, _ in out]
    if len(ids) != len(set(ids)):
        sys.exit("Duplicate Unique IDs in taxonomy")
    # GAM value matching is case-insensitive, so e.g. "abc" and "ABC" would collide.
    if len({i.lower() for i in ids}) != len(ids):
        sys.exit("Unique IDs collide case-insensitively")
    return out


def get_key_by_id(svc, ad_manager, key_id: int) -> int:
    sb = ad_manager.StatementBuilder(version="v202605")
    sb.Where("id = :id")
    sb.WithBindVariable("id", key_id)
    resp = svc.getCustomTargetingKeysByStatement(sb.ToStatement())
    results = getattr(resp, "results", None) or []
    if not results:
        sys.exit(f"No custom targeting key with id {key_id}")
    k = results[0]
    if str(getattr(k, "status", "ACTIVE")) != "ACTIVE":
        sys.exit(f"Key {key_id} ('{k.name}') is {k.status}, not ACTIVE")
    log.info("Found key id=%d → name='%s' type=%s", key_id, k.name, getattr(k, "type", "?"))
    return key_id


def find_or_create_key(svc, ad_manager, key_name: str, dry_run: bool) -> int | None:
    sb = ad_manager.StatementBuilder(version="v202605")
    sb.Where("name = :name AND status = 'ACTIVE'")
    sb.WithBindVariable("name", key_name)
    sb.Limit(10)
    resp = svc.getCustomTargetingKeysByStatement(sb.ToStatement())
    results = getattr(resp, "results", None) or []
    if results:
        k = results[0]
        log.info("Found key '%s' → id=%s type=%s", key_name, k.id, getattr(k, "type", "?"))
        return int(k.id)
    if dry_run:
        log.info("[dry-run] key '%s' does not exist — would create it (PREDEFINED)", key_name)
        return None
    created = svc.createCustomTargetingKeys([{
        "name": key_name,
        "displayName": KEY_DISPLAY_NAME,
        "type": "PREDEFINED",
    }])
    key_id = int(created[0].id)
    log.info("Created key '%s' → id=%d", key_name, key_id)
    return key_id


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--tsv", default=DEFAULT_TSV)
    p.add_argument("--key-name", default=DEFAULT_KEY)
    p.add_argument("--key-id", type=int, help="Target this key id (overrides --key-name; never creates)")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    taxonomy = load_taxonomy(args.tsv)
    log.info("Loaded %d taxonomy rows from %s", len(taxonomy), args.tsv)

    client, ad_manager = get_soap_client()
    svc = client.GetService("CustomTargetingService", version="v202605")

    if args.key_id:
        key_id = get_key_by_id(svc, ad_manager, args.key_id)
    else:
        key_id = find_or_create_key(svc, ad_manager, args.key_name, args.dry_run)
    existing = fetch_existing_values(svc, ad_manager, key_id) if key_id else {}
    # Index case-insensitively — GAM treats value names that way.
    existing_ci = {k.lower(): v | {"name": k} for k, v in existing.items()}

    to_add = [(i, n) for i, n in taxonomy if i.lower() not in existing_ci]
    to_fix = [
        (existing_ci[i.lower()], n) for i, n in taxonomy
        if i.lower() in existing_ci and existing_ci[i.lower()]["displayName"] != n
    ]
    log.info(
        "Taxonomy: %d | already on key: %d | to add: %d | display names to fix: %d",
        len(taxonomy), len(taxonomy) - len(to_add), len(to_add), len(to_fix),
    )
    for i, n in to_add[:10]:
        log.info("  add  %-8s → %s", i, n)
    if len(to_add) > 10:
        log.info("  ... and %d more", len(to_add) - 10)
    for v, n in to_fix[:10]:
        log.info("  fix  %-8s : %r → %r", v["name"], v["displayName"], n)

    if args.dry_run:
        log.info("[dry-run] no writes.")
        return

    created = 0
    for s in range(0, len(to_add), BATCH_SIZE):
        batch = to_add[s:s + BATCH_SIZE]
        res = svc.createCustomTargetingValues([
            {"customTargetingKeyId": key_id, "name": i, "displayName": n, "matchType": "EXACT"}
            for i, n in batch
        ])
        created += len(res or [])
        log.info("Created batch %d (%d values)", s // BATCH_SIZE + 1, len(batch))
        time.sleep(0.5)

    updated = 0
    for s in range(0, len(to_fix), BATCH_SIZE):
        batch = to_fix[s:s + BATCH_SIZE]
        res = svc.updateCustomTargetingValues([
            {"id": v["id"], "customTargetingKeyId": key_id, "name": v["name"],
             "displayName": n, "matchType": v["matchType"]}
            for v, n in batch
        ])
        updated += len(res or [])
        time.sleep(0.5)

    final = fetch_existing_values(svc, ad_manager, key_id)
    missing = [i for i, _ in taxonomy if i.lower() not in {k.lower() for k in final}]
    log.info("Done. Created %d, renamed %d. Key %s now has %d active values; %d taxonomy IDs missing.",
             created, updated, key_id, len(final), len(missing))
    if missing:
        sys.exit(f"Missing after sync: {missing[:20]}")


if __name__ == "__main__":
    main()
