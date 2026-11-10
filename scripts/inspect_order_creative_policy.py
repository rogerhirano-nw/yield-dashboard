#!/usr/bin/env python3
"""Read-only: every creative on a GAM order, with its policy flags.

Built for GAM's "creatives ... contain malware / will not serve" alerts: for
each line item on the order it lists each LICA (status), the creative's
`policyLabels` (MALWARE_IN_CREATIVE vs MALWARE_IN_LANDING_PAGE vs ...), its
type/size/created date, the click-through URL, and every URL found in its
snippet / asset — so you can tell which domain the scanner tripped on and
whether only the "(copy)" creatives or the originals too are flagged.

Usage:
    python3 scripts/inspect_order_creative_policy.py <order id | name fragment>
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from collections import Counter

from googleads import ad_manager, oauth2

V = "v202605"
URL_RE = re.compile(r"""(?:https?:)?//[^\s"'<>)\\]+""", re.I)


def _client():
    sa = json.loads(os.environ["GAM_SERVICE_ACCOUNT_JSON"])
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(sa, f)
        kf = f.name
    oc = oauth2.GoogleServiceAccountClient(kf, "https://www.googleapis.com/auth/dfp")
    return ad_manager.AdManagerClient(oc, "NewsweekDashboard/1.0",
                                      network_code=os.environ["GAM_NETWORK_ID"])


def _q(svc, method, where, **binds):
    out, off = [], 0
    while True:
        sb = ad_manager.StatementBuilder(version=V).Where(where).Limit(500).Offset(off)
        for k, v in binds.items():
            sb = sb.WithBindVariable(k, v)
        page = list(getattr(getattr(svc, method)(sb.ToStatement()), "results", []) or [])
        out += page
        if len(page) < 500:
            return out
        off += 500


def _g(o, name):
    try:
        return o[name]
    except Exception:  # noqa: BLE001
        return getattr(o, name, None)


def _dt(v):
    if v is None:
        return "—"
    d = v.date
    return f"{d.year:04d}-{d.month:02d}-{d.day:02d} {v.hour:02d}:{v.minute:02d}"


def _urls(c):
    blobs = []
    for f in ("htmlSnippet", "snippet", "expandedSnippet", "vastXmlUrl",
              "vastRedirectUrl", "destinationUrl"):
        s = _g(c, f)
        if s:
            blobs.append(str(s))
    for f in ("primaryImageAsset", "asset"):
        a = _g(c, f)
        if a is not None:
            for k in ("assetUrl", "fileName"):
                if _g(a, k):
                    blobs.append(str(_g(a, k)))
    for v in (_g(c, "creativeTemplateVariableValues") or []):
        for k in ("value", "asset"):
            x = _g(v, k)
            if x is not None:
                blobs.append(str(x))
    found = []
    for b in blobs:
        for u in URL_RE.findall(b):
            if u not in found:
                found.append(u)
    return found


def main() -> int:
    arg = sys.argv[1]
    cl = _client()
    o_svc = cl.GetService("OrderService", version=V)
    li_svc = cl.GetService("LineItemService", version=V)
    lica_svc = cl.GetService("LineItemCreativeAssociationService", version=V)
    cr_svc = cl.GetService("CreativeService", version=V)

    if arg.isdigit():
        orders = _q(o_svc, "getOrdersByStatement", "id = :i", i=int(arg))
    else:
        orders = _q(o_svc, "getOrdersByStatement", "name LIKE :n", n=f"%{arg}%")
    if not orders:
        print(f"!! no order matches {arg!r}")
        return 1

    label_tally = Counter()
    domain_tally = Counter()
    flagged_domains = Counter()
    for order in orders:
        print("=" * 78)
        print(f"ORDER {order.id}: {order.name}  (status {order.status})")
        print("=" * 78)
        lis = _q(li_svc, "getLineItemsByStatement", "orderId = :o", o=order.id)
        for li in lis:
            print(f"\nLI {li.id} [{li.status}] {li.name}")
            licas = _q(lica_svc, "getLineItemCreativeAssociationsByStatement",
                       "lineItemId = :i", i=li.id)
            for la in licas:
                cs = _q(cr_svc, "getCreativesByStatement", "id = :i", i=la.creativeId)
                if not cs:
                    print(f"  creative {la.creativeId} not found (LICA {la.status})")
                    continue
                c = cs[0]
                labels = [str(x) for x in (_g(c, "policyLabels") or [])]
                for lb in labels:
                    label_tally[lb] += 1
                tname = getattr(getattr(c, "_xsd_type", None), "name", type(c).__name__)
                sz = _g(c, "size")
                flag = "!! FLAGGED " if labels else "   "
                print(f" {flag}{c.id} {c.name}")
                print(f"      type {tname}  size {sz.width}x{sz.height}  LICA {la.status}"
                      f"  created {_dt(_g(c, 'creationDateTime'))}"
                      f"  modified {_dt(_g(c, 'lastModifiedDateTime'))}")
                print(f"      policyLabels: {labels or '[]'}")
                decl = _g(c, "thirdPartyDataDeclaration")
                if decl is not None:
                    print(f"      ATP declaration: {_g(decl, 'declarationType')} "
                          f"{list(_g(decl, 'thirdPartyCompanyIds') or [])}")
                print(f"      dest: {_g(c, 'destinationUrl')}")
                us = _urls(c)
                for u in us:
                    print(f"        url: {u}")
                    host = re.sub(r"^(?:https?:)?//", "", u).split("/")[0].split("?")[0].lower()
                    domain_tally[host] += 1
                    if labels:
                        flagged_domains[host] += 1
                trk = _g(c, "thirdPartyImpressionTrackingUrls") or []
                for t in trk:
                    print(f"        imp tracker: {t}")

    print("\n" + "=" * 78)
    print("SUMMARY")
    print(f"policy labels: {dict(label_tally) or 'none'}")
    print("domains on flagged creatives (count) vs on all creatives:")
    for d, n in flagged_domains.most_common():
        print(f"  {d}: {n} flagged / {domain_tally[d]} total")
    return 0


if __name__ == "__main__":
    sys.exit(main())
