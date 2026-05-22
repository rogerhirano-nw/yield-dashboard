"""
OpenSincera API client.

Pulls publisher quality + ecosystem metadata from https://open.sincera.io/api.
Unlike the SSP clients in this repo, OpenSincera is a *reference* feed: it
tells us how the wider RTB ecosystem and our peer publishers look (A2CR,
ads-in-view, ad refresh, page weight, etc.), not our own revenue.

Auth:  Bearer token from env var OPENSINCERA_TOKEN.

Endpoints covered:
  /ecosystem        — single snapshot of the global ecosystem
  /publishers       — per-publisher quality metrics (by id or domain)
  /adsystems        — list of known ad systems
  /mapping_modules  — Prebid module → ad-system mapping

The API is rate-limited; per-call sleep is conservative (0.2s) for the
publisher loop. Mass-token rotation is explicitly banned by Sincera —
do not parallelise across tokens.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Iterable

import pandas as pd
import requests

logger = logging.getLogger(__name__)

_BASE_URL          = "https://open.sincera.io/api"
_PER_CALL_SLEEP_S  = 0.2
_REQUEST_TIMEOUT_S = 30


class OpenSinceraClient:

    def __init__(self, token: str | None = None) -> None:
        token = token or os.environ.get("OPENSINCERA_TOKEN")
        if not token:
            raise RuntimeError(
                "OPENSINCERA_TOKEN is not set. Add it to .env or your "
                "orchestrator's secret store."
            )
        self._headers = {"Authorization": f"Bearer {token}"}

    # ------------------------------------------------------------------
    # Low-level fetch
    # ------------------------------------------------------------------

    def _get(self, endpoint: str, params: dict | None = None) -> dict | list:
        url = f"{_BASE_URL}/{endpoint}"
        resp = requests.get(url, headers=self._headers, params=params,
                            timeout=_REQUEST_TIMEOUT_S)
        if not resp.ok:
            logger.error("OpenSincera %s -> %s: %s",
                         endpoint, resp.status_code, resp.text[:300])
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # Ecosystem
    # ------------------------------------------------------------------

    def get_ecosystem(self) -> pd.DataFrame:
        """One-row DataFrame summarising the OpenSincera ecosystem snapshot.

        Nested fields (pbjs_ad_unit_media_types, pbjs_major_versions,
        header_wrappers, recently_updated_publishers) are JSON-encoded into
        string columns so they round-trip cleanly through SQLite/Postgres.
        """
        data = self._get("ecosystem")
        if not isinstance(data, dict):
            logger.warning("Unexpected ecosystem payload type: %s", type(data))
            return pd.DataFrame()

        import json
        row = {}
        for k, v in data.items():
            if isinstance(v, (dict, list)):
                row[k] = json.dumps(v)
            else:
                row[k] = v
        return pd.DataFrame([row])

    # ------------------------------------------------------------------
    # Publishers
    # ------------------------------------------------------------------

    def get_publisher_by_domain(self, domain: str) -> dict | None:
        """Single publisher record by domain. Returns None on 404."""
        try:
            data = self._get("publishers", params={"domain": domain})
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code == 404:
                logger.info("OpenSincera: no publisher record for %s", domain)
                return None
            raise
        # API may return a dict or a single-element list; normalise.
        if isinstance(data, list):
            return data[0] if data else None
        return data or None

    def get_publishers(self, domains: Iterable[str]) -> pd.DataFrame:
        """Pull metadata for each domain in `domains`. Skips 404s.

        Nested fields (categories, device_level_metrics, similar_publishers)
        are JSON-encoded into string columns for cache compatibility.
        """
        import json

        rows: list[dict] = []
        for domain in domains:
            try:
                rec = self.get_publisher_by_domain(domain)
            except Exception:
                logger.exception("OpenSincera: failed to fetch %s", domain)
                rec = None
            if rec is None:
                continue

            flat = {}
            for k, v in rec.items():
                if isinstance(v, (dict, list)):
                    flat[k] = json.dumps(v)
                else:
                    flat[k] = v
            flat["queried_domain"] = domain
            rows.append(flat)

            time.sleep(_PER_CALL_SLEEP_S)

        if not rows:
            return pd.DataFrame()
        return pd.DataFrame(rows)

    # ------------------------------------------------------------------
    # Ad systems
    # ------------------------------------------------------------------

    def get_adsystems(self) -> pd.DataFrame:
        """DataFrame of known ad systems. Flattens image.url out of image dict."""
        data = self._get("adsystems")
        if not isinstance(data, list) or not data:
            return pd.DataFrame()
        rows = []
        for entry in data:
            row = {k: v for k, v in entry.items() if k != "image"}
            img = entry.get("image") or {}
            row["image_url"] = img.get("url") if isinstance(img, dict) else None
            rows.append(row)
        return pd.DataFrame(rows)

    # ------------------------------------------------------------------
    # Prebid module mappings
    # ------------------------------------------------------------------

    def get_mapping_modules(self) -> pd.DataFrame:
        """DataFrame of Prebid module → ad-system mappings."""
        data = self._get("mapping_modules")
        if not isinstance(data, list) or not data:
            return pd.DataFrame()
        return pd.DataFrame(data)
