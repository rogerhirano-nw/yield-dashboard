"""Related-audience discovery for the ROS AV custom-audience line (one-off).

Pulls, via the SOAP AudienceSegmentService (there is no similarity API in
GAM — "related" is keyword + provider siblings):
  1. The line item's CURRENT state: status, flight, frequency caps, creative
     sizes, and the audience-segment IDs actually targeted (walked out of
     targeting.customTargeting's AudienceSegmentCriteria nodes).
  2. Ground truth on the currently targeted segments (name, provider, size,
     CPM cost, status) — the report export only shows names.
  3. CANDIDATE segments related to what the line already runs:
       - AI tier (any provider): third-party ACTIVE segments whose name
         matches AI/ML phrases mirroring the current plan's working families.
       - Broad tier (current providers only): decision-maker / C-suite /
         tech-buyer phrases, restricted to providers already on the plan so
         the catalog-wide firehose stays out.
       - Newsweek FIRST_PARTY segments (free reach; small list, pulled whole).
     Candidates exclude already-targeted IDs, are deduped across terms, and
     ranked: AI-tier hit (2) + already-licensed provider (1), then size.

Output contract (parsed from the Actions job log by the analysis step):
  - human-readable summary sections first (these also go to the PR comment);
  - the full candidate sheet as TSV between the literal marker lines
    BEGIN_RELATED_AUDIENCES_TSV / END_RELATED_AUDIENCES_TSV (capped at
    MAX_TSV_ROWS, biggest-first; per-query truncation is logged, never silent).

Runs in Actions via .github/workflows/pull_related_audiences.yml (the
pull_index_ob_requests.yml pattern) — GAM creds live only in repo secrets.
Read-only: no mutate calls anywhere.
"""

import json
import os
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
env_file = REPO_ROOT / ".env"
if env_file.exists():
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

from googleads import ad_manager, oauth2  # noqa: E402

V = "v202605"
LI_ID = int(os.environ.get("RELATED_AUD_LI_ID", "7300490129"))
PAGE = 500
MAX_PAGES_PER_QUERY = 4          # 2,000 rows/query; totals logged so cuts are visible
MAX_TSV_ROWS = 2500

# AI tier — mirrors the families already delivering on the line (LiveRamp OAN
# "Professionals Using AI", TL1 "AI Events", Bombora AI/ML, BlueWhale "Active
# Research > AI"). Catalog names are title-case; PQL LIKE terms match that.
AI_TERMS = [
    "Artificial Intelligence",
    "Machine Learning",
    "Generative AI",
    "AI Events",
    "Users of AI",
    "AI Tools",
    "AI Software",
    "Professionals Using AI",
    "Data Science",
    "ChatGPT",
]
# Broad tier — high-volume phrases; restricted to providers already licensed
# on the plan (derived at runtime from the currently targeted segments).
BROAD_TERMS = [
    "Decision Maker",
    "C-Suite",
    "C Level",
    "Technology Buyer",
    "IT Decision",
    "Big Tech",
    "Business Decision",
]


def make_client():
    sa = json.loads(os.environ["GAM_SERVICE_ACCOUNT_JSON"])
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(sa, f)
        key_file = f.name
    oc = oauth2.GoogleServiceAccountClient(key_file, "https://www.googleapis.com/auth/dfp")
    return ad_manager.AdManagerClient(
        oc, "NewsweekDashboard/1.0", network_code=os.environ["GAM_NETWORK_ID"]
    )


def walk_audience_criteria(node, out):
    """Collect (operator, [segment ids]) from a CustomCriteriaSet tree."""
    if node is None:
        return
    ids = getattr(node, "audienceSegmentIds", None)
    if ids:
        out.append((str(getattr(node, "operator", "") or ""), [int(i) for i in ids]))
    for child in (getattr(node, "children", None) or []):
        walk_audience_criteria(child, out)


def seg_row(s):
    dp = getattr(getattr(s, "dataProvider", None), "name", None) or ""
    cost = getattr(s, "cost", None)
    cpm = None
    currency = ""
    if cost is not None and getattr(cost, "microAmount", None) is not None:
        cpm = int(cost.microAmount) / 1e6
        currency = str(getattr(cost, "currencyCode", "") or "")
    size = getattr(s, "size", None)
    mob = getattr(s, "mobileWebSize", None)
    return {
        "id": int(s.id),
        "name": " ".join(str(getattr(s, "name", "") or "").split()),  # strip \t\n
        "provider": dp,
        "type": str(getattr(s, "type", "") or type(s).__name__),
        "status": str(getattr(s, "status", "") or ""),
        "size": int(size) if size is not None else 0,
        "mobile_size": int(mob) if mob is not None else 0,
        "cpm": cpm,
        "currency": currency,
        "license": str(getattr(s, "licenseType", "") or ""),
        "approval": str(getattr(s, "approvalStatus", "") or ""),
    }


def run_query(svc, where, label):
    """Page through getAudienceSegmentsByStatement; return (rows, total).

    Resilient: a PQL fault on one query logs and returns empty instead of
    killing the whole pull (an Actions round-trip is expensive); ORDER BY
    size falls back to id if the sort column is rejected.
    """
    rows, total = [], 0
    for order_col in ("size", "id"):
        sb = ad_manager.StatementBuilder(version=V)
        sb.Where(where).OrderBy(order_col, ascending=False).Limit(PAGE)
        rows, total, pages = [], 0, 0
        try:
            while pages < MAX_PAGES_PER_QUERY:
                resp = svc.getAudienceSegmentsByStatement(sb.ToStatement())
                total = int(getattr(resp, "totalResultSetSize", 0) or 0)
                results = getattr(resp, "results", None) or []
                if not results:
                    break
                rows.extend(seg_row(s) for s in results)
                pages += 1
                sb.offset += sb.limit
                if sb.offset >= total:
                    break
            break  # query succeeded (possibly empty) — no fallback needed
        except Exception as exc:  # PQL fault — retry ordered by id, then give up
            msg = " ".join(str(exc).split())[:200]
            print(f"  [{label}] QUERY FAILED (order by {order_col}): {msg}")
            rows, total = [], 0
    dropped = max(0, total - len(rows))
    print(f"  [{label}] {len(rows):>5} fetched / {total:>6} total"
          + (f"  (TRUNCATED, {dropped} dropped)" if dropped else ""))
    return rows, total


def pql_quote(s):
    return "'" + s.replace("'", "''") + "'"


def fmt_millions(n):
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n // 1000}k"
    return str(n)


def main():
    client = make_client()
    li_svc = client.GetService("LineItemService", version=V)
    seg_svc = client.GetService("AudienceSegmentService", version=V)

    # ---- 1. the line item as it stands -------------------------------------
    sb = ad_manager.StatementBuilder(version=V)
    sb.Where("id = :id").WithBindVariable("id", LI_ID).Limit(1)
    resp = li_svc.getLineItemsByStatement(sb.ToStatement())
    results = getattr(resp, "results", None) or []
    if not results:
        print(f"ERROR: line item {LI_ID} not found")
        sys.exit(1)
    li = results[0]

    print(f"=== Line item {LI_ID} — current state ===")
    print(f"name:     {getattr(li, 'name', '?')}")
    print(f"type/pri: {getattr(li, 'lineItemType', '?')} / priority {getattr(li, 'priority', '?')}")
    print(f"status:   {getattr(li, 'status', '?')}  archived={getattr(li, 'isArchived', '?')}")
    st, en = getattr(li, "startDateTime", None), getattr(li, "endDateTime", None)

    def _dt(d):
        try:
            return f"{d.date.year}-{d.date.month:02d}-{d.date.day:02d}"
        except Exception:
            return "unlimited" if d is None else str(d)

    print(f"flight:   {_dt(st)} -> {_dt(en)}  "
          f"(unlimited end: {getattr(li, 'unlimitedEndDateTime', '?')})")
    goal = getattr(li, "primaryGoal", None)
    if goal is not None:
        print(f"goal:     {getattr(goal, 'goalType', '?')} {getattr(goal, 'units', '?')} "
              f"{getattr(goal, 'unitType', '?')}")
    caps = getattr(li, "frequencyCaps", None) or []
    if caps:
        for c in caps:
            print(f"freq cap: {getattr(c, 'maxImpressions', '?')} imps per "
                  f"{getattr(c, 'numTimeUnits', '?')} {getattr(c, 'timeUnit', '?')}")
    else:
        print("freq cap: NONE SET")
    sizes = []
    for ph in (getattr(li, "creativePlaceholders", None) or []):
        sz = getattr(ph, "size", None)
        if sz is not None:
            sizes.append(f"{getattr(sz, 'width', '?')}x{getattr(sz, 'height', '?')}")
    print(f"sizes:    {', '.join(sizes) if sizes else '?'}")

    crits = []
    walk_audience_criteria(getattr(getattr(li, "targeting", None), "customTargeting", None), crits)
    cur_ids = sorted({i for _, ids in crits for i in ids})
    by_op = {}
    for op, ids in crits:
        by_op.setdefault(op or "?", set()).update(ids)
    ops = ", ".join(f"{op}: {len(ids)}" for op, ids in sorted(by_op.items()))
    print(f"\n=== Current audience targeting: {len(cur_ids)} distinct segment ids ({ops}) ===")
    if not cur_ids:
        print("WARNING: no AudienceSegmentCriteria found on this LI's customTargeting tree")

    # ---- 2. ground truth on the targeted segments --------------------------
    cur_rows = []
    for i in range(0, len(cur_ids), 300):
        chunk = cur_ids[i:i + 300]
        rows, _ = run_query(
            seg_svc, f"id IN ({','.join(map(str, chunk))})", f"current ids {i}-{i + len(chunk)}"
        )
        cur_rows.extend(rows)
    found = {r["id"] for r in cur_rows}
    missing = [i for i in cur_ids if i not in found]
    if missing:
        print(f"NOTE: {len(missing)} targeted ids not returned by AudienceSegmentService: {missing}")
    cur_rows.sort(key=lambda r: (-r["size"]))
    print(f"\n{'id':>12}  {'size':>8}  {'cpm':>6}  {'status':<8}  name")
    for r in cur_rows:
        cpm = f"{r['cpm']:.2f}" if r["cpm"] is not None else "—"
        print(f"{r['id']:>12}  {fmt_millions(r['size']):>8}  {cpm:>6}  {r['status']:<8}  "
              f"{r['provider']} :: {r['name'][:110]}")
    cur_providers = sorted({r["provider"] for r in cur_rows if r["provider"]})
    print(f"\ncurrent providers ({len(cur_providers)}): {'; '.join(cur_providers)}")

    # ---- 3. candidate discovery --------------------------------------------
    print("\n=== Query coverage (candidates) ===")
    seen = {}   # id -> row (+ terms/tier annotations)

    def absorb(rows, term, tier):
        for r in rows:
            e = seen.setdefault(r["id"], {**r, "terms": set(), "tiers": set()})
            e["terms"].add(term)
            e["tiers"].add(tier)

    for kw in AI_TERMS:
        where = (f"type = 'THIRD_PARTY' AND status = 'ACTIVE' "
                 f"AND name LIKE {pql_quote('%' + kw + '%')}")
        rows, _ = run_query(seg_svc, where, f"AI: {kw}")
        absorb(rows, kw, "ai")

    if cur_providers:
        prov_in = ", ".join(pql_quote(p) for p in cur_providers)
        for kw in BROAD_TERMS:
            where = (f"type = 'THIRD_PARTY' AND status = 'ACTIVE' "
                     f"AND dataProviderName IN ({prov_in}) "
                     f"AND name LIKE {pql_quote('%' + kw + '%')}")
            rows, _ = run_query(seg_svc, where, f"broad: {kw}")
            absorb(rows, kw, "broad")

    rows, _ = run_query(seg_svc, "type = 'FIRST_PARTY' AND status = 'ACTIVE'", "first-party (all)")
    absorb(rows, "(first-party)", "1p")

    cur_set = set(cur_ids)
    cands = [e for e in seen.values() if e["id"] not in cur_set]
    for e in cands:
        e["ai"] = 1 if "ai" in e["tiers"] else 0
        e["licensed"] = 1 if e["provider"] in cur_providers else 0
        e["fp"] = 1 if "1p" in e["tiers"] else 0
        e["score"] = 2 * e["ai"] + e["licensed"] + 3 * e["fp"]  # 1P floats to top
    cands.sort(key=lambda e: (-e["score"], -e["size"]))
    print(f"\ncandidates: {len(cands)} distinct after dedupe "
          f"(+{len(seen) - len(cands)} already targeted, excluded)")
    by_prov = {}
    for e in cands:
        by_prov[e["provider"] or "?"] = by_prov.get(e["provider"] or "?", 0) + 1
    print("by provider: " + "; ".join(f"{p}: {n}" for p, n in
                                      sorted(by_prov.items(), key=lambda x: -x[1])[:20]))

    print(f"\n=== Top candidates (of {len(cands)}; score = 2*AI-tier + licensed-provider "
          f"+ 3*first-party, then size) ===")
    print(f"{'score':>5}  {'id':>12}  {'size':>8}  {'cpm':>6}  name")
    for e in cands[:120]:
        cpm = f"{e['cpm']:.2f}" if e["cpm"] is not None else "—"
        print(f"{e['score']:>5}  {e['id']:>12}  {fmt_millions(e['size']):>8}  {cpm:>6}  "
              f"{e['provider']} :: {e['name'][:105]}")

    # ---- 4. full sheet, machine-readable ------------------------------------
    print(f"\nBEGIN_RELATED_AUDIENCES_TSV")
    cols = ["id", "score", "ai", "licensed", "fp", "provider", "type", "status",
            "size", "mobile_size", "cpm", "currency", "license", "approval", "terms", "name"]
    print("\t".join(cols))
    for e in cands[:MAX_TSV_ROWS]:
        e2 = {**e, "terms": "|".join(sorted(e["terms"])),
              "cpm": "" if e["cpm"] is None else f"{e['cpm']:.2f}"}
        print("\t".join(str(e2[c]) for c in cols))
    print("END_RELATED_AUDIENCES_TSV")
    if len(cands) > MAX_TSV_ROWS:
        print(f"NOTE: TSV capped at {MAX_TSV_ROWS} of {len(cands)} candidates (score/size order)")


if __name__ == "__main__":
    main()
