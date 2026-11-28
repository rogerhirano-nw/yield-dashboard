"""In-memory copy of the cache tables, kept fresh in the background.

The whole cache DB is ~70 MB, and data only changes when a refresh writes
(~20 times a day). So instead of querying per request (the Streamlit app's
~30 serial reads, ~20 s cold), the API holds each table as a DataFrame and a
background thread checks Postgres' per-table write counters every
``POLL_SECONDS``; only tables that changed are re-read, in parallel. A request
never waits on the database except on the very first boot.

Anything derived from the tables (``derived()``) is memoized per data version,
so each view's heavy prep runs once per refresh, not once per click.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

import pandas as pd
import sqlalchemy as sa

log = logging.getLogger("next.data")
POLL_SECONDS = int(os.environ.get("DATA_POLL_SECONDS", "60"))

# Tables the views read. Column projections keep the big DV tables small.
TABLES: dict[str, str | None] = {
    "gam_campaigns": None,
    "gam_pmp_deals": None,
    "gam_deal_bid_daily": None,
    "gam_pa_metadata": None,
    "magnite_deal_daily": None,
    "magnite_site_daily": None,
    "magnite_dsp_daily": None,
    "magnite_deal_demand": None,
    "pubmatic_deals": None,
    "pmp_last_bid_date": None,
    "ttd_chumba": None,
    "dv_attention": "line_item_id, line_item_name, order_name, attention_index, date",
    "dv_ivt": "line_item_id, line_item_name, order_name, date, traffic_validity, monitored_ads",
    "opensincera_ecosystem": None,
    "opensincera_publishers": None,
    "opensincera_adsystems": None,
    "opensincera_modules": None,
    "dashboard_settings": None,
}


def _url() -> str:
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        raise RuntimeError("DATABASE_URL is not set")
    return url


class Store:
    def __init__(self) -> None:
        self.engine = sa.create_engine(_url(), pool_size=6, max_overflow=2, pool_pre_ping=True,
                                       pool_recycle=300)
        self.frames: dict[str, pd.DataFrame] = {}
        self.marks: dict[str, int] = {}
        self.version = 0
        self.loaded_at: float | None = None
        self._derived: dict[str, tuple[int, object]] = {}
        self._lock = threading.Lock()
        self._ready = threading.Event()
        self.warmers: list[Callable[["Store"], object]] = []

    # ── loading ──
    def _marks(self) -> dict[str, int]:
        q = sa.text("SELECT relname, n_tup_ins + n_tup_upd + n_tup_del AS w "
                    "FROM pg_stat_user_tables WHERE schemaname = 'public'")
        with self.engine.connect() as c:
            return {r.relname: int(r.w) for r in c.execute(q)}

    def _read(self, table: str) -> pd.DataFrame:
        cols = TABLES.get(table) or "*"
        with self.engine.connect() as c:
            try:
                return pd.read_sql(sa.text(f'SELECT {cols} FROM "{table}"'), c)
            except Exception:  # noqa: BLE001 — projection drifted, or table missing
                c.rollback()
                try:
                    return pd.read_sql(sa.text(f'SELECT * FROM "{table}"'), c)
                except Exception:  # noqa: BLE001
                    log.warning("table %s unavailable", table)
                    return pd.DataFrame()

    def refresh(self, force: bool = False) -> list[str]:
        marks = self._marks()
        stale = [t for t in TABLES if force or t not in self.frames
                 or marks.get(t, -1) != self.marks.get(t, -2)]
        if not stale:
            return []
        t0 = time.time()
        with ThreadPoolExecutor(max_workers=6) as ex:
            got = dict(zip(stale, ex.map(self._read, stale)))
        with self._lock:
            self.frames.update(got)
            self.marks.update({t: marks.get(t, -1) for t in stale})
            self.version += 1
            self.loaded_at = time.time()
        log.info("reloaded %d table(s) in %.1fs: %s", len(stale), time.time() - t0, ", ".join(stale))
        self._ready.set()  # before warming: the warmers read tables via get()
        # Build every view's prepared data now, so no request pays for it.
        for w in self.warmers:
            try:
                w(self)
            except Exception:  # noqa: BLE001
                log.exception("warmer %s failed", getattr(w, "__name__", w))
        log.info("warm in %.1fs", time.time() - t0)
        self._ready.set()
        return stale

    def start(self) -> None:
        def loop():
            while True:
                try:
                    self.refresh()
                except Exception:  # noqa: BLE001
                    log.exception("refresh failed")
                time.sleep(POLL_SECONDS)
        threading.Thread(target=loop, name="data-refresh", daemon=True).start()

    # ── access ──
    def get(self, table: str) -> pd.DataFrame:
        self._ready.wait(timeout=120)
        return self.frames.get(table, pd.DataFrame())

    def derived(self, key: str, build: Callable[["Store"], object]):
        """``build(store)`` once per data version (e.g. the prepared campaigns frame)."""
        v = self.version
        hit = self._derived.get(key)
        if hit and hit[0] == v:
            return hit[1]
        val = build(self)
        self._derived[key] = (v, val)
        return val


def settings(st: "Store") -> dict:
    """Dashboard settings for the current data version (dashboard_settings 'main')."""
    from .settings import load_settings

    def build(s: "Store"):
        df = s.get("dashboard_settings")
        row = df[df["key"] == "main"]["value"] if not df.empty and "key" in df else []
        return load_settings(row.iloc[0] if len(row) else None)
    return st.derived("settings", build)


_store: Store | None = None


def store() -> Store:
    global _store
    if _store is None:
        _store = Store()
        _store.start()
    return _store
