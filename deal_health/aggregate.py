"""
Compute aggregations from a list of UnhealthyDeal records. Pure functions —
no I/O, no datetime.now(). Renderer consumes the Payload this builds.
"""

from __future__ import annotations

from collections import Counter
from datetime import date
from typing import Iterable

from .colors import TOP_DARK_ADVERTISERS_LIMIT
from .models import AdvertiserRollup, Payload, SSPRollup, UnhealthyDeal


def _filter_excluded_sellers(deals: Iterable[UnhealthyDeal]) -> list[UnhealthyDeal]:
    """House + Unknown are excluded from per-seller breakouts (and from the
    seller_count KPI). They DO count toward total_deals / total_bid_requests."""
    return [d for d in deals if d.parsed.seller not in ("House", "Unknown")]


def _safe_share(numerator: int, denominator: int) -> float:
    return (numerator / denominator) if denominator > 0 else 0.0


def build_payload(
    deals: Iterable[UnhealthyDeal],
    *,
    report_date: date,
    lookback_days: int,
    csv_url: str,
    dashboard_url: str,
) -> Payload:
    deals = tuple(deals)

    total_deals = len(deals)
    total_bid_requests = sum(d.bid_requests for d in deals)

    # KPI: seller_count excludes House + Unknown.
    sellers = {d.parsed.seller for d in _filter_excluded_sellers(deals)}
    seller_count = len(sellers)

    # By-SSP rollup, descending by bid_requests. Uses source_ssp (the data
    # source, which we know definitively), not parsed.ssp (which can lie).
    ssp_counts = Counter()
    ssp_requests = Counter()
    for d in deals:
        ssp_counts[d.source_ssp] += 1
        ssp_requests[d.source_ssp] += d.bid_requests
    by_ssp = tuple(
        SSPRollup(
            ssp=ssp,
            deal_count=ssp_counts[ssp],
            bid_requests=req,
            share_of_total=_safe_share(req, total_bid_requests),
        )
        for ssp, req in sorted(ssp_requests.items(), key=lambda kv: -kv[1])
    )

    # Top dark advertisers — top N by bid_requests. Missing advertiser → "Unattributed".
    adv_counts = Counter()
    adv_requests = Counter()
    for d in deals:
        key = d.parsed.advertiser or "Unattributed"
        adv_counts[key] += 1
        adv_requests[key] += d.bid_requests
    top_advertisers_pairs = sorted(adv_requests.items(), key=lambda kv: -kv[1])[:TOP_DARK_ADVERTISERS_LIMIT]
    top_dark_advertisers = tuple(
        AdvertiserRollup(advertiser=name, deal_count=adv_counts[name], bid_requests=req)
        for name, req in top_advertisers_pairs
    )

    # Top DSP by share of bid requests. Missing DSP → grouped under "Unknown".
    dsp_requests = Counter()
    for d in deals:
        dsp_requests[d.parsed.dsp or "Unknown"] += d.bid_requests
    if dsp_requests:
        top_dsp_name, top_dsp_req = max(dsp_requests.items(), key=lambda kv: kv[1])
        top_dsp = (top_dsp_name, _safe_share(top_dsp_req, total_bid_requests))
    else:
        top_dsp = ("—", 0.0)

    return Payload(
        report_date=report_date,
        lookback_days=lookback_days,
        deals=deals,
        by_ssp=by_ssp,
        top_dark_advertisers=top_dark_advertisers,
        top_dsp=top_dsp,
        total_deals=total_deals,
        total_bid_requests=total_bid_requests,
        seller_count=seller_count,
        csv_url=csv_url,
        dashboard_url=dashboard_url,
    )
