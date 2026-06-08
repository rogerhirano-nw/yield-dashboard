"""Update GAM custom targeting key 'bmb' with Bombora topic taxonomy values.

Usage:
    # From a committed CSV (default — used by GitHub Actions):
    python scripts/update_bmb_kvps.py --csv data/bombora_penguin_20260608.csv

    # From the full Excel file:
    python scripts/update_bmb_kvps.py --xlsx /path/to/Bombora_Topic_Taxonomy.xlsx --sheet full

    # Dry-run (no writes):
    python scripts/update_bmb_kvps.py --csv data/bombora_penguin_20260608.csv --dry-run

The script diffs the taxonomy against the values already in GAM and only
creates what's missing, so it's safe to re-run.  Each Bombora topic becomes
one GAM custom targeting value:  name=<Topic_ID>  displayName=<Topic_Name>.
"""

import argparse
import csv
import json
import logging
import os
import sys
import tempfile
import time

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)

BATCH_SIZE = 500  # GAM recommends ≤500 objects per createCustomTargetingValues call


def load_taxonomy_csv(path: str) -> list[tuple[str, str]]:
    """Return list of (topic_id, topic_name) from a CSV with a topic_id column."""
    rows = []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            tid = str(row.get("topic_id", row.get("Topic_ID", ""))).strip()
            name = str(row.get("topic_name", row.get("Topic_Name", ""))).strip()
            if tid and name:
                rows.append((tid, name))
    return rows


def load_taxonomy_xlsx(path: str, sheet: str) -> list[tuple[str, str]]:
    """Return list of (topic_id, topic_name) from an Excel taxonomy file."""
    try:
        import openpyxl  # type: ignore
    except ImportError:
        sys.exit("openpyxl not installed — pip install openpyxl")

    sheet_map = {
        "full": "Bombora Full Taxonomy",
        "penguin": "PenguinTopics",
    }
    sheet_name = sheet_map.get(sheet, sheet)
    wb = openpyxl.load_workbook(path, read_only=True)
    if sheet_name not in wb.sheetnames:
        sys.exit(f"Sheet '{sheet_name}' not found; available: {wb.sheetnames}")
    ws = wb[sheet_name]
    rows = []
    header = None
    for row in ws.iter_rows(values_only=True):
        if header is None:
            header = [str(c).strip() if c else "" for c in row]
            continue
        if len(row) < 4:
            continue
        # Theme, Category, Topic_ID, Topic_Name, ...
        tid = str(row[2]).strip() if row[2] is not None else ""
        name = str(row[3]).strip() if row[3] is not None else ""
        if tid and name:
            rows.append((tid, name))
    return rows


def get_soap_client():
    sa_json = os.environ.get("GAM_SERVICE_ACCOUNT_JSON")
    if not sa_json:
        sys.exit("GAM_SERVICE_ACCOUNT_JSON env var not set")
    network_id = os.environ.get("GAM_NETWORK_ID")
    if not network_id:
        sys.exit("GAM_NETWORK_ID env var not set")

    try:
        from googleads import ad_manager, oauth2  # type: ignore
    except ImportError:
        sys.exit("googleads not installed — pip install googleads")

    key_data = json.loads(sa_json)
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(key_data, f)
        key_file = f.name

    oauth2_client = oauth2.GoogleServiceAccountClient(
        key_file, "https://www.googleapis.com/auth/dfp"
    )
    client = ad_manager.AdManagerClient(
        oauth2_client, "NewsweekDashboard/1.0", network_code=network_id
    )
    return client, ad_manager


def find_key_id(svc, ad_manager, key_name: str) -> int:
    sb = ad_manager.StatementBuilder(version="v202605")
    sb.Where("name = :name AND status = 'ACTIVE'")
    sb.WithBindVariable("name", key_name)
    sb.Limit(10)
    resp = svc.getCustomTargetingKeysByStatement(sb.ToStatement())
    results = getattr(resp, "results", None) or []
    if not results:
        sys.exit(
            f"No active custom targeting key named '{key_name}' found in GAM network."
        )
    if len(results) > 1:
        log.warning("Multiple keys named '%s' — using first (id=%s)", key_name, results[0].id)
    key_id = int(results[0].id)
    log.info("Found key '%s' → id=%d", key_name, key_id)
    return key_id


def fetch_existing_value_names(svc, ad_manager, key_id: int) -> set[str]:
    """Return the set of value `name` strings already in GAM for this key."""
    existing: set[str] = set()
    sb = ad_manager.StatementBuilder(version="v202605")
    sb.Where("customTargetingKeyId = :kid AND status = 'ACTIVE'")
    sb.WithBindVariable("kid", key_id)
    sb.Limit(500)
    while True:
        resp = svc.getCustomTargetingValuesByStatement(sb.ToStatement())
        results = getattr(resp, "results", None) or []
        if not results:
            break
        for v in results:
            existing.add(str(getattr(v, "name", "")))
        sb.offset += sb.limit
        if sb.offset >= getattr(resp, "totalResultSetSize", 0):
            break
    log.info("Existing values in GAM for key id=%d: %d", key_id, len(existing))
    return existing


def create_values_batch(svc, key_id: int, batch: list[tuple[str, str]], dry_run: bool) -> int:
    """Create a batch of (name, displayName) values. Returns count created."""
    if dry_run:
        log.info("  [dry-run] would create %d values", len(batch))
        return len(batch)

    objects = []
    for name, display_name in batch:
        objects.append({
            "customTargetingKeyId": key_id,
            "name": name,
            "displayName": display_name[:255],
            "matchType": "EXACT",
        })
    result = svc.createCustomTargetingValues(objects)
    created = len(result) if result else 0
    return created


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--csv", metavar="PATH", help="CSV file with topic_id/topic_name columns")
    src.add_argument("--xlsx", metavar="PATH", help="Excel taxonomy file from Bombora")
    parser.add_argument(
        "--sheet", default="full", choices=["full", "penguin"],
        help="Which Excel sheet to read (only used with --xlsx; default: full)",
    )
    parser.add_argument("--key-name", default="bmb", help="GAM targeting key name (default: bmb)")
    parser.add_argument("--dry-run", action="store_true", help="Report changes without writing")
    args = parser.parse_args()

    if args.csv:
        taxonomy = load_taxonomy_csv(args.csv)
        log.info("Loaded %d topics from CSV %s", len(taxonomy), args.csv)
    else:
        taxonomy = load_taxonomy_xlsx(args.xlsx, args.sheet)
        log.info("Loaded %d topics from Excel %s (sheet=%s)", len(taxonomy), args.xlsx, args.sheet)

    if not taxonomy:
        sys.exit("No topics loaded — check your input file.")

    client, ad_manager = get_soap_client()
    svc = client.GetService("CustomTargetingService", version="v202605")

    key_id = find_key_id(svc, ad_manager, args.key_name)
    existing = fetch_existing_value_names(svc, ad_manager, key_id)

    to_add = [(tid, name) for tid, name in taxonomy if tid not in existing]
    log.info(
        "Taxonomy: %d  |  Already in GAM: %d  |  To add: %d",
        len(taxonomy), len(existing), len(to_add),
    )

    if not to_add:
        log.info("Nothing to do — GAM is already up to date.")
        return

    if args.dry_run:
        log.info("[dry-run] First 10 values that would be added:")
        for tid, name in to_add[:10]:
            log.info("  %s  →  %s", tid, name)
        if len(to_add) > 10:
            log.info("  ... and %d more", len(to_add) - 10)
        log.info("[dry-run] Total that would be added: %d", len(to_add))
        return

    total_created = 0
    for i in range(0, len(to_add), BATCH_SIZE):
        batch = to_add[i : i + BATCH_SIZE]
        log.info("Creating batch %d/%d (%d values)…",
                 i // BATCH_SIZE + 1, -(-len(to_add) // BATCH_SIZE), len(batch))
        n = create_values_batch(svc, key_id, batch, dry_run=False)
        total_created += n
        if i + BATCH_SIZE < len(to_add):
            time.sleep(0.5)  # be gentle with the API

    log.info("Done. Created %d new values for key '%s'.", total_created, args.key_name)


if __name__ == "__main__":
    main()
