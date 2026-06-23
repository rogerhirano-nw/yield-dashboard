#!/usr/bin/env python3
"""Read-only diagnostic: inspect the live creatives on the Infiniti sponsor-logo LI.

Lists LICAs (with status) for LI 7336465381 and dumps each creative's
name/type/status + an htmlSnippet markers scan (carrier-reposition,
MRC beacon, cfg.viewUrl). No writes.
"""
import os
import sys
from pathlib import Path

# --- load .env into os.environ (scripts read creds from env) ---
envp = Path(__file__).resolve().parent.parent / ".env"
for line in envp.read_text().splitlines():
    line = line.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    k, _, v = line.partition("=")
    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from gam_client import GAMClient  # noqa: E402
from googleads import ad_manager  # noqa: E402

LI = sys.argv[1] if len(sys.argv) > 1 else "7336465381"
V = "v202605"

gc = GAMClient()
client = gc._get_soap_client()

# 1) LICAs WITH status
lica_svc = client.GetService("LineItemCreativeAssociationService", version=V)
sb = ad_manager.StatementBuilder(version=V).Where(f"lineItemId = {int(LI)}").Limit(100)
resp = lica_svc.getLineItemCreativeAssociationsByStatement(sb.ToStatement())
licas = list(getattr(resp, "results", []) or [])
print(f"=== LI {LI}: {len(licas)} creative association(s) ===")
cre_ids = []
for la in licas:
    cid = getattr(la, "creativeId", None)
    cre_ids.append(cid)
    print(f"  creative {cid}  LICA.status={getattr(la,'status',None)}  "
          f"sizes={getattr(la,'sizes',None) and len(la.sizes)}")

# 2) Each creative: name/type/status + snippet markers
cre_svc = client.GetService("CreativeService", version=V)
ids_str = ", ".join(str(int(c)) for c in cre_ids if c is not None)
sb2 = ad_manager.StatementBuilder(version=V).Where(f"id IN ({ids_str})").Limit(100)
cresp = cre_svc.getCreativesByStatement(sb2.ToStatement())
MARKERS = {
    "carrier-reposition (position:fixed glue)": ["position:fixed", "position: fixed"],
    "carrier sync (rAF/scroll glue)":           ["requestAnimationFrame", "syncCarrier", "glueCarrier", "reposition"],
    "MRC beacon (IntersectionObserver)":        ["IntersectionObserver", "__nwSponsorViewable", "nw_sponsor_logo_viewable"],
    "viewable DOM marker":                      ["nw-sponsor-logo-viewed"],
    "watcher boot":                             ["nw-sponsor-logo-boot"],
    "pixels once-guard":                        ["__nwSponsorPx"],
    "breadcrumb selector":                      ["ResponsiveBreadcrumbs"],
}
for c in getattr(cresp, "results", []) or []:
    cid = getattr(c, "id", None)
    name = getattr(c, "name", "")
    ctype = type(c).__name__
    snippet = getattr(c, "htmlSnippet", None) or getattr(c, "snippet", None) or ""
    print(f"\n--- creative {cid} [{ctype}] '{name}' ---")
    print(f"    snippet length: {len(snippet)} chars")
    for label, needles in MARKERS.items():
        hit = next((n for n in needles if n in snippet), None)
        print(f"    [{'x' if hit else ' '}] {label}" + (f"  ({hit})" if hit else ""))
    # cfg.viewUrl value
    import re
    m = re.search(r"viewUrl\s*[:=]\s*([\"'])(.*?)\1", snippet)
    if m:
        val = m.group(2)
        print(f"    cfg.viewUrl = {'<EMPTY>' if not val else val[:80]}")
    else:
        print("    cfg.viewUrl: (not found in snippet)")
    # dump snippet to a file for full read
    out = Path("/tmp") / f"sponsor_creative_{cid}.html"
    out.write_text(snippet)
    print(f"    full snippet -> {out}")
