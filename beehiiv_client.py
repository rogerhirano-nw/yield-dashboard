"""
beehiiv API client.

Pulls newsletter audience + per-send performance from the beehiiv v2 API
(https://api.beehiiv.com/v2). Unlike the SSP clients, beehiiv is not an ad
feed: it reports the *audience* behind newsletter ad inventory (how many
active subscribers, how a given send performed) rather than revenue.

Auth:  API key from env var BEEHIIV_API_KEY, sent as `Authorization: Bearer`.
       This is the REST API key, NOT the beehiiv MCP server — that one is
       browser-OAuth and can't be used from a scheduled job. See the
       "MCP servers" section of CLAUDE.md.

Endpoints covered:
  GET /publications                      — audience snapshot per publication
  GET /publications/{id}/posts           — per-post email + web stats

Both take `expand` to include the `stats` object; the documented encoding is
a JSON array (`expand=["stats"]`). Rate limits are advertised through
RateLimit-* response headers; 429/5xx are retried with exponential backoff.
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import date, datetime, timedelta
from typing import Iterator
from zoneinfo import ZoneInfo

import pandas as pd
import requests

logger = logging.getLogger(__name__)

_BASE_URL          = "https://api.beehiiv.com/v2"
_REQUEST_TIMEOUT_S = 30
_PAGE_SIZE         = 100          # API max
_MAX_PAGES         = 200          # hard stop; 20k posts is far past any window
_RETRY_STATUSES    = (429, 500, 502, 503, 504)
_MAX_RETRIES       = 5

# beehiiv timestamps are Unix epochs. The dashboard's "date" semantics are
# Eastern everywhere (see the Streamlit Cloud section of CLAUDE.md), so
# epoch->date conversion goes through ET, not the container's UTC clock.
_ET = ZoneInfo("America/New_York")

# How far back to pull posts. Posts are written with _safe_replace (full
# TRUNCATE+append), so widening this can't duplicate rows — unlike the
# append-with-DELETE tables, which are bound by the retention_days rule.
DEFAULT_POST_WINDOW_DAYS = 90


class BeehiivAPIError(RuntimeError):
    """Raised when beehiiv returns a payload we can't use."""


# ----------------------------------------------------------------------
# Pure transforms (no network — these are what the tests pin)
# ----------------------------------------------------------------------

def epoch_to_et_date(ts: object) -> str | None:
    """Unix epoch (seconds) -> 'YYYY-MM-DD' in America/New_York.

    Returns None for missing/unparseable values rather than raising: a post
    with no publish_date is still a row worth keeping."""
    if ts is None or ts == "":
        return None
    try:
        seconds = float(ts)
    except (TypeError, ValueError):
        return None
    try:
        return datetime.fromtimestamp(seconds, tz=_ET).date().isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def _as_json(value: object) -> str | None:
    """JSON-encode list/dict fields so they round-trip through SQLite/Postgres
    as text columns (same approach as opensincera_client)."""
    if value is None:
        return None
    if isinstance(value, (list, dict)):
        return json.dumps(value)
    return str(value)


def flatten_publication(pub: dict, snapshot_date: str) -> dict:
    """One publication record -> one flat row for `beehiiv_publications`.

    `stats` is the expand=["stats"] sub-object; its absence yields None
    metrics rather than a KeyError, so a key without stats scope still
    produces identifiable rows (and the caller warns)."""
    stats = pub.get("stats") or {}
    return {
        "publication_id":               pub.get("id"),
        "name":                         pub.get("name"),
        "organization_name":            pub.get("organization_name"),
        "referral_program_enabled":     pub.get("referral_program_enabled"),
        "created_date":                 epoch_to_et_date(pub.get("created")),
        "active_subscriptions":         stats.get("active_subscriptions"),
        "active_premium_subscriptions": stats.get("active_premium_subscriptions"),
        "active_free_subscriptions":    stats.get("active_free_subscriptions"),
        "average_open_rate":            stats.get("average_open_rate"),
        "average_click_rate":           stats.get("average_click_rate"),
        "total_sent":                   stats.get("total_sent"),
        "total_unique_opened":          stats.get("total_unique_opened"),
        "total_clicked":                stats.get("total_clicked"),
        "date":                         snapshot_date,
    }


def flatten_post(post: dict, publication_id: str) -> dict:
    """One post record -> one flat row for `beehiiv_posts`.

    Flattens stats.email.* and stats.web.* into `email_*` / `web_*` columns.
    stats.clicks (the per-URL breakdown) is intentionally dropped — it is a
    variable-length list that would need its own table; see the CLAUDE.md
    note if per-link reporting is ever wanted."""
    stats = post.get("stats") or {}
    email = stats.get("email") or {}
    web   = stats.get("web") or {}
    return {
        "post_id":                     post.get("id"),
        "publication_id":              publication_id,
        "title":                       post.get("title"),
        "subtitle":                    post.get("subtitle"),
        "slug":                        post.get("slug"),
        "authors":                     _as_json(post.get("authors")),
        "content_tags":                _as_json(post.get("content_tags")),
        "status":                      post.get("status"),
        "platform":                    post.get("platform"),
        "audience":                    post.get("audience"),
        "subject_line":                post.get("subject_line"),
        "web_url":                     post.get("web_url"),
        "split_tested":                post.get("split_tested"),
        "created_date":                epoch_to_et_date(post.get("created")),
        "displayed_date":              epoch_to_et_date(post.get("displayed_date")),
        "recipient_count":             post.get("recipient_count"),
        "email_recipients":            email.get("recipients"),
        "email_delivered":             email.get("delivered"),
        "email_opens":                 email.get("opens"),
        "email_unique_opens":          email.get("unique_opens"),
        "email_open_rate":             email.get("open_rate"),
        "email_clicks":                email.get("clicks"),
        "email_unique_clicks":         email.get("unique_clicks"),
        "email_verified_clicks":       email.get("verified_clicks"),
        "email_unique_verified_clicks": email.get("unique_verified_clicks"),
        "email_click_rate":            email.get("click_rate"),
        "email_unsubscribes":          email.get("unsubscribes"),
        "email_spam_reports":          email.get("spam_reports"),
        "web_views":                   web.get("views"),
        "web_clicks":                  web.get("clicks"),
        "upgrades":                    stats.get("upgrades"),
        # publish_date is the row's date key — the day the send went out.
        "date":                        epoch_to_et_date(post.get("publish_date")),
    }


def post_within_window(post: dict, cutoff: date) -> bool:
    """True when the post published on/after `cutoff`.

    A post with no parseable publish_date is kept (True): dropping rows on a
    missing timestamp would silently shrink the table, and the caller only
    uses this to decide when to stop paginating a publish_date-desc list."""
    d = epoch_to_et_date(post.get("publish_date"))
    if d is None:
        return True
    return d >= cutoff.isoformat()


# ----------------------------------------------------------------------
# Client
# ----------------------------------------------------------------------

class BeehiivClient:

    def __init__(self, api_key: str | None = None) -> None:
        api_key = api_key or os.environ.get("BEEHIIV_API_KEY")
        if not api_key:
            raise RuntimeError(
                "BEEHIIV_API_KEY is not set. Add it to .env or your "
                "orchestrator's secret store. (The beehiiv MCP server is "
                "browser-OAuth and cannot be used from a scheduled job.)"
            )
        self._session = requests.Session()
        self._session.headers.update({
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
        })

    # ------------------------------------------------------------------
    # Low-level fetch
    # ------------------------------------------------------------------

    def _get(self, path: str, params: dict | None = None) -> dict:
        """GET with exponential backoff on 429/5xx.

        beehiiv advertises budget through RateLimit-Remaining; we honour an
        explicit Retry-After when present and otherwise back off 2^n seconds
        (1, 2, 4, 8, 16) — the same shape as the Magnite client's ladder."""
        url = f"{_BASE_URL}/{path.lstrip('/')}"
        last_exc: Exception | None = None

        for attempt in range(_MAX_RETRIES):
            try:
                resp = self._session.get(url, params=params,
                                         timeout=_REQUEST_TIMEOUT_S)
            except requests.RequestException as exc:
                last_exc = exc
                if attempt == _MAX_RETRIES - 1:
                    raise
                logger.info("beehiiv %s: %s — retrying (%d/%d)",
                            path, exc, attempt + 1, _MAX_RETRIES)
                time.sleep(2 ** attempt)
                continue

            if resp.status_code in _RETRY_STATUSES and attempt < _MAX_RETRIES - 1:
                wait = 2 ** attempt
                retry_after = resp.headers.get("Retry-After")
                if retry_after:
                    try:
                        wait = max(wait, int(float(retry_after)))
                    except ValueError:
                        pass
                logger.info("beehiiv %s -> %s — backing off %ds (%d/%d)",
                            path, resp.status_code, wait, attempt + 1, _MAX_RETRIES)
                time.sleep(wait)
                continue

            if not resp.ok:
                logger.error("beehiiv %s -> %s: %s",
                             path, resp.status_code, resp.text[:300])
            resp.raise_for_status()

            remaining = resp.headers.get("RateLimit-Remaining")
            if remaining is not None and remaining.isdigit() and int(remaining) < 5:
                logger.warning("beehiiv rate-limit budget low: %s remaining", remaining)

            try:
                return resp.json()
            except ValueError as exc:
                raise BeehiivAPIError(
                    f"beehiiv {path} returned non-JSON: {resp.text[:200]}"
                ) from exc

        raise BeehiivAPIError(f"beehiiv {path} failed after "
                              f"{_MAX_RETRIES} attempts") from last_exc

    def _paginate(self, path: str, params: dict | None = None) -> Iterator[dict]:
        """Yield every record from a paginated list endpoint.

        Stops on the API's own total_pages, on an empty page, and at
        _MAX_PAGES — so a pagination bug upstream can't spin forever."""
        page = 1
        while page <= _MAX_PAGES:
            payload = self._get(path, {**(params or {}),
                                       "limit": _PAGE_SIZE, "page": page})
            records = payload.get("data") or []
            if not records:
                return
            yield from records

            total_pages = payload.get("total_pages")
            if isinstance(total_pages, int) and page >= total_pages:
                return
            page += 1
        logger.warning("beehiiv %s: hit the %d-page cap — results truncated",
                       path, _MAX_PAGES)

    # ------------------------------------------------------------------
    # Publications
    # ------------------------------------------------------------------

    def get_publications(self, snapshot_date: str | None = None) -> pd.DataFrame:
        """Audience snapshot for every publication the API key can see.

        One row per publication, stamped with `snapshot_date` (ET today by
        default) — the API exposes only current counts, so history has to be
        accumulated by snapshotting, like opensincera_ecosystem."""
        snapshot_date = snapshot_date or datetime.now(_ET).date().isoformat()
        records = list(self._paginate("publications",
                                      {"expand": '["stats"]'}))
        if not records:
            logger.warning("beehiiv: no publications returned")
            return pd.DataFrame()

        if not any(r.get("stats") for r in records):
            logger.error(
                "beehiiv publications came back WITHOUT stats — every audience "
                "metric will be null. Check that the API key has the "
                "publications:read scope and that `expand` is still encoded as "
                "a JSON array.")

        df = pd.DataFrame([flatten_publication(r, snapshot_date) for r in records])
        logger.info("beehiiv: %d publication(s), %s active subscriptions total",
                    len(df), df["active_subscriptions"].sum(skipna=True))
        return df

    def publication_ids(self) -> list[str]:
        """Every publication id visible to the key, for the posts loop."""
        return [r.get("id") for r in self._paginate("publications") if r.get("id")]

    # ------------------------------------------------------------------
    # Posts
    # ------------------------------------------------------------------

    def get_posts(self, publication_id: str,
                  window_days: int = DEFAULT_POST_WINDOW_DAYS,
                  today: date | None = None) -> pd.DataFrame:
        """Per-post email + web stats for the last `window_days`.

        Walks the publish_date-desc list and stops at the first post older
        than the cutoff — so a publication with years of archive costs one
        page, not its whole history."""
        today = today or datetime.now(_ET).date()
        cutoff = today - timedelta(days=window_days)

        rows: list[dict] = []
        saw_stats = False
        for post in self._paginate(
            f"publications/{publication_id}/posts",
            {"expand": '["stats"]', "status": "confirmed",
             "order_by": "publish_date", "direction": "desc"},
        ):
            if not post_within_window(post, cutoff):
                break
            saw_stats = saw_stats or bool(post.get("stats"))
            rows.append(flatten_post(post, publication_id))

        if not rows:
            logger.warning("beehiiv: no posts in the last %d days for %s",
                           window_days, publication_id)
            return pd.DataFrame()

        if not saw_stats:
            logger.error(
                "beehiiv posts for %s came back WITHOUT stats — every open/click "
                "metric will be null. Check the API key's posts:read scope and "
                "that `expand` is still encoded as a JSON array.", publication_id)

        df = pd.DataFrame(rows)
        logger.info("beehiiv: %d post(s) for %s since %s",
                    len(df), publication_id, cutoff.isoformat())
        return df

    def get_posts_for_all(self, publication_ids: list[str] | None = None,
                          window_days: int = DEFAULT_POST_WINDOW_DAYS,
                          today: date | None = None) -> pd.DataFrame:
        """get_posts across every publication, concatenated.

        A failure on one publication is logged and skipped rather than
        aborting the sweep — one broken newsletter shouldn't cost the rest."""
        ids = publication_ids if publication_ids is not None else self.publication_ids()
        frames: list[pd.DataFrame] = []
        for pub_id in ids:
            try:
                df = self.get_posts(pub_id, window_days=window_days, today=today)
            except Exception:
                logger.exception("beehiiv: failed to fetch posts for %s", pub_id)
                continue
            if not df.empty:
                frames.append(df)
        if not frames:
            return pd.DataFrame()
        return pd.concat(frames, ignore_index=True)
