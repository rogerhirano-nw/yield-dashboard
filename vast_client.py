"""VAST XML duration parser.

GAM's Creative API returns null duration for many VAST/3rd-party creatives —
the actual duration lives in the VAST XML at the upstream URL. This module
fetches those URLs and extracts the <Duration> element, with wrapper-chain
following up to a small recursion limit.

Used by refresh_cache.refresh_gam_vast_durations() to fill in
gam_creatives.duration_seconds for VAST creatives where GAM didn't have
duration cached locally.
"""

from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from typing import Optional
from urllib.parse import urlparse

import requests

logger = logging.getLogger(__name__)

# Cap a single creative's parse pipeline:
# - 3 wrapper redirects max (VAST 4.x supports VAST chains; 3 is a sane cap)
# - 5s per HTTP request
# - 10s total budget per creative including chained fetches
_MAX_WRAPPER_DEPTH = 3
_HTTP_TIMEOUT = 5.0
_USER_AGENT = (
    "Mozilla/5.0 (compatible; NewsweekDashboard-VAST/1.0; "
    "+https://newsweek.com/yield-dashboard)"
)

# VAST `<Duration>` elements are HH:MM:SS or HH:MM:SS.fff. Some non-standard
# tags emit a plain integer seconds value — accept that too.
_DURATION_HMS_RE = re.compile(r"^\s*(\d{1,2}):(\d{1,2}):(\d{1,2}(?:\.\d+)?)\s*$")
_DURATION_MS_RE  = re.compile(r"^\s*(\d{1,2}):(\d{1,2}(?:\.\d+)?)\s*$")


def _parse_duration_str(s: Optional[str]) -> Optional[float]:
    """Parse a VAST <Duration> string to seconds.

    Accepts HH:MM:SS, HH:MM:SS.fff, MM:SS, or a plain numeric value.
    Returns None for any unparseable input.
    """
    if not s:
        return None
    s = s.strip()
    m = _DURATION_HMS_RE.match(s)
    if m:
        h, mn, sec = m.groups()
        try:
            return float(h) * 3600 + float(mn) * 60 + float(sec)
        except ValueError:
            return None
    m = _DURATION_MS_RE.match(s)
    if m:
        mn, sec = m.groups()
        try:
            return float(mn) * 60 + float(sec)
        except ValueError:
            return None
    # Last-ditch: plain integer / float seconds.
    try:
        return float(s)
    except ValueError:
        return None


def _is_safe_url(url: str) -> bool:
    """Reject non-http(s) URLs and obviously malformed entries before fetching."""
    if not isinstance(url, str) or len(url) < 10:
        return False
    try:
        u = urlparse(url)
    except Exception:
        return False
    return u.scheme in ("http", "https") and bool(u.netloc)


def parse_vast_duration(
    url: str,
    *,
    timeout: float = _HTTP_TIMEOUT,
    max_depth: int = _MAX_WRAPPER_DEPTH,
    session: Optional[requests.Session] = None,
) -> Optional[float]:
    """Fetch a VAST URL and return the ad duration in seconds.

    Follows VAST wrappers (`<VASTAdTagURI>`) up to `max_depth` levels.
    Returns None on any error: HTTP failure, malformed XML, missing
    Duration element, or exhausted wrapper depth.

    Safe to call from a thread pool — uses a per-call requests.Session
    only if not provided.
    """
    if not _is_safe_url(url):
        return None
    owns_session = session is None
    s = session or requests.Session()
    try:
        visited: set[str] = set()
        for _depth in range(max_depth + 1):
            if url in visited:
                return None
            visited.add(url)
            try:
                resp = s.get(
                    url,
                    timeout=timeout,
                    headers={"User-Agent": _USER_AGENT},
                    allow_redirects=True,
                )
                resp.raise_for_status()
            except requests.RequestException:
                return None
            content = resp.content
            if not content:
                return None
            try:
                root = ET.fromstring(content)
            except ET.ParseError:
                return None
            # Look for <Duration> anywhere in the tree. VAST namespaces vary
            # (default ns vs. explicit), so match on local-name-ends-with.
            duration_elem = None
            wrapper_elem = None
            for elem in root.iter():
                local = elem.tag.rsplit("}", 1)[-1] if "}" in elem.tag else elem.tag
                if local == "Duration" and duration_elem is None:
                    duration_elem = elem
                elif local == "VASTAdTagURI" and wrapper_elem is None:
                    wrapper_elem = elem
                if duration_elem is not None and wrapper_elem is not None:
                    break
            if duration_elem is not None:
                return _parse_duration_str(duration_elem.text)
            if wrapper_elem is not None and wrapper_elem.text:
                url = wrapper_elem.text.strip()
                if not _is_safe_url(url):
                    return None
                continue
            return None
        return None
    finally:
        if owns_session:
            s.close()
