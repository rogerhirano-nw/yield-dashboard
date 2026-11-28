#!/usr/bin/env python3
"""Build final reports and screenshot decks without anyone asking.

Run daily by `.github/workflows/campaign_reports.yml` (Azure Logic App
`la-sched-yield-campaign-reports`, 07:30 ET), and on demand from the
dashboard's Tools tab for an older date range:

- **final**: every Direct / PG order whose flight ENDED in the window gets
  `final_campaign_report.py` + the client workbook (`final_report_workbook.py`).
  The daily run's window is yesterday, so a report exists the morning after
  the campaign's last day.
- **deck**: every on-site line item that STARTED in the window gets a capture
  on an automatically picked article (`capture_screenshots.py --article-url
  auto`) and the client deck (`screenshots_auto_spec.py` +
  `build_screenshots_deck.py`). Apple News and newsletter lines are skipped —
  there's no newsweek.com page to shoot them on.

Each result lands in Postgres table `campaign_reports` (one row per kind +
order + line item, file stored as bytea), which the Tools tab lists and serves.
Anything already stored is skipped unless `--force`. Everything here is
read-only against GAM.

Usage:
  python scripts/campaign_reports_auto.py --kind final            # ended yesterday
  python scripts/campaign_reports_auto.py --kind deck             # started in the last 3 days
  python scripts/campaign_reports_auto.py --kind final --from 2026-10-01 --to 2026-10-31
  python scripts/campaign_reports_auto.py --kind final --orders 4194246183
  python scripts/campaign_reports_auto.py --kind deck --line-items 7440274079
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

import dashboard_logic as dl  # noqa: E402

ET = ZoneInfo("America/New_York")
V = "v202605"
ORDER_PREFIXES = ("Newsweek_Direct", "Newsweek_PG")
# Same internal test/QA orders the dashboard hides (dashboard.py _EXCLUDED_ORDER_IDS).
EXCLUDED_ORDERS = {"3648897741", "4082002976"}

DDL = """
CREATE TABLE IF NOT EXISTS campaign_reports (
    id            BIGSERIAL PRIMARY KEY,
    kind          TEXT NOT NULL,          -- 'final' | 'deck'
    order_id      TEXT NOT NULL,
    line_item_id  TEXT NOT NULL DEFAULT '',
    order_name    TEXT,
    advertiser    TEXT,
    campaign      TEXT,
    start_date    DATE,
    end_date      DATE,
    file_name     TEXT,
    file_bytes    BYTEA,
    summary_md    TEXT,
    status        TEXT NOT NULL,          -- 'ok' | 'failed'
    note          TEXT,
    run_url       TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (kind, order_id, line_item_id)
)
"""


# ── pure helpers (tested) ────────────────────────────────────────────────
def default_window(kind: str, today: date) -> tuple[date, date]:
    """final: yesterday (the morning after the last day). deck: the last 3 days
    including today, so a missed morning is picked up the next day."""
    if kind == "final":
        y = today - timedelta(days=1)
        return y, y
    return today - timedelta(days=2), today


def is_report_order(order_id: str, name: str) -> bool:
    return str(order_id) not in EXCLUDED_ORDERS and (name or "").startswith(ORDER_PREFIXES)


def is_on_site_line(name: str) -> bool:
    """A line a newsweek.com screenshot can show: not Apple News, not a newsletter."""
    n = (name or "").lower()
    if "newsletter" in n or "bulletin" in n:
        return False
    return dl.derive_format(None, name or "") != "Apple News"


def name_parts(name: str) -> tuple[str, str]:
    tok = (name or "").split("_")
    if len(tok) > 8:
        return (" ".join(tok[7].replace("-", " ").split()),
                " ".join(tok[8].replace("-", " ").split()))
    return "", name or ""


# ── GAM discovery ────────────────────────────────────────────────────────
def _soap():
    from gam_client import GAMClient
    return GAMClient()._get_soap_client()


def _page(svc, method, where):
    from googleads import ad_manager
    sb = ad_manager.StatementBuilder(version=V).Where(where).Limit(500)
    out = []
    while True:
        resp = getattr(svc, method)(sb.ToStatement())
        res = list(getattr(resp, "results", []) or [])
        out.extend(res)
        sb.offset += sb.limit
        if not res or sb.offset >= getattr(resp, "totalResultSetSize", 0):
            return out


def _d(dt) -> date | None:
    d = getattr(dt, "date", None) if dt is not None else None
    return date(d.year, d.month, d.day) if d is not None else None


def _pql_range(field: str, start: date, end: date) -> str:
    return (f"{field} >= '{start.isoformat()}T00:00:00' AND "
            f"{field} < '{(end + timedelta(days=1)).isoformat()}T00:00:00'")


def ended_orders(client, start: date, end: date) -> list[dict]:
    svc = client.GetService("OrderService", version=V)
    rows = _page(svc, "getOrdersByStatement",
                 _pql_range("endDateTime", start, end) + " AND isArchived = false")
    out = []
    for o in rows:
        oid, name = str(o.id), getattr(o, "name", "") or ""
        # APPROVED covers finished orders too; DRAFT never ran, CANCELED is dead
        if is_report_order(oid, name) and getattr(o, "status", "") in ("APPROVED", "PAUSED"):
            out.append({"order_id": oid, "order_name": name,
                        "start": _d(getattr(o, "startDateTime", None)),
                        "end": _d(getattr(o, "endDateTime", None))})
    return out


def started_lines(client, start: date, end: date) -> list[dict]:
    li_svc = client.GetService("LineItemService", version=V)
    lis = _page(li_svc, "getLineItemsByStatement",
                _pql_range("startDateTime", start, end)
                + " AND status IN ('READY', 'DELIVERING', 'DELIVERY_EXTENDED', 'COMPLETED')")
    if not lis:
        return []
    o_svc = client.GetService("OrderService", version=V)
    ids = sorted({str(li.orderId) for li in lis})
    names = {}
    for i in range(0, len(ids), 200):
        for o in _page(o_svc, "getOrdersByStatement", f"id IN ({', '.join(ids[i:i + 200])})"):
            names[str(o.id)] = getattr(o, "name", "") or ""
    out = []
    for li in lis:
        oid = str(li.orderId)
        if is_report_order(oid, names.get(oid, "")) and is_on_site_line(li.name):
            out.append({"order_id": oid, "order_name": names[oid], "line_item_id": str(li.id),
                        "line_item_name": li.name,
                        "start": _d(getattr(li, "startDateTime", None)),
                        "end": _d(getattr(li, "endDateTime", None))})
    return out


# ── storage ──────────────────────────────────────────────────────────────
def engine():
    import sqlalchemy as sa
    e = sa.create_engine(os.environ["DATABASE_URL"], pool_pre_ping=True)
    with e.begin() as c:
        c.exec_driver_sql(DDL)
    return e


def already(e, kind: str, order_id: str, line_item_id: str = "") -> bool:
    import sqlalchemy as sa
    with e.connect() as c:
        return c.execute(sa.text(
            "SELECT 1 FROM campaign_reports WHERE kind=:k AND order_id=:o "
            "AND line_item_id=:l AND status='ok'"),
            {"k": kind, "o": order_id, "l": line_item_id}).first() is not None


def store(e, row: dict) -> None:
    import sqlalchemy as sa
    row = {"line_item_id": "", "file_name": None, "file_bytes": None, "summary_md": None,
           "note": None, "run_url": os.environ.get("RUN_URL"), **row}
    cols = ["kind", "order_id", "line_item_id", "order_name", "advertiser", "campaign",
            "start_date", "end_date", "file_name", "file_bytes", "summary_md", "status",
            "note", "run_url"]
    with e.begin() as c:
        c.execute(sa.text(
            f"INSERT INTO campaign_reports ({', '.join(cols)}) VALUES "
            f"({', '.join(':' + x for x in cols)}) "
            "ON CONFLICT (kind, order_id, line_item_id) DO UPDATE SET "
            + ", ".join(f"{x}=EXCLUDED.{x}" for x in cols[3:]) + ", created_at=now()"),
            {k: row.get(k) for k in cols})


# ── builders ─────────────────────────────────────────────────────────────
def _run(cmd: list[str], env: dict) -> subprocess.CompletedProcess:
    p = subprocess.run(cmd, env={**os.environ, **env}, cwd=REPO, text=True,
                       capture_output=True, timeout=1800)
    sys.stdout.write(p.stdout[-6000:])
    sys.stderr.write(p.stderr[-3000:])
    return p


def build_final(o: dict, work: Path) -> dict:
    import final_report_workbook as frw
    md, js = work / "final_report.md", work / "final_report.json"
    p = _run([sys.executable, "scripts/final_campaign_report.py",
              "--markdown", str(md), "--out", str(js)],
             {"FINAL_REPORT_ORDER_ID": o["order_id"],
              "FINAL_REPORT_END_DATE": o["end"].isoformat() if o.get("end") else ""})
    adv, camp = name_parts(o["order_name"])
    base = {"kind": "final", "order_id": o["order_id"], "order_name": o["order_name"],
            "advertiser": adv, "campaign": camp, "start_date": o.get("start"),
            "end_date": o.get("end")}
    if p.returncode != 0 or not js.exists():
        return {**base, "status": "failed", "note": (p.stderr or p.stdout)[-500:]}
    report = json.loads(js.read_text())
    ae = json.loads((REPO / "settings.json").read_text()).get("ae_names", {})
    a = frw.derive(report, ae)
    from build_final_report_xlsx import build
    safe = "".join(c for c in f"{a['advertiser']} {a['campaign']}" if c not in '/\\:*?"<>|')
    xlsx = work / f"{safe} final report.xlsx"
    build(report, str(xlsx), name=f"{a['advertiser']} — {a['campaign']}",
          advertiser=a["advertiser"], campaign=a["campaign"], seller=a["seller"],
          flight=a["flight"], line_label=a["line_label"], ctr_target=a["ctr_target"],
          logo=None, template=str(REPO / "templates" / "final_report_template.xlsx"),
          delivery_note=None, daily_note=None, omit_measurement=False, revenue=False)
    return {**base, "advertiser": a["advertiser"], "campaign": a["campaign"],
            "file_name": xlsx.name, "file_bytes": xlsx.read_bytes(),
            "summary_md": md.read_text() if md.exists() else None, "status": "ok"}


def build_deck(li: dict, work: Path) -> dict:
    import screenshots_auto_spec as sas
    shots = work / "shots"
    p = _run([sys.executable, "scripts/capture_screenshots.py"],
             {"CAPTURE_LINE_ITEM": li["line_item_id"], "CAPTURE_ARTICLE_URL": "auto",
              "CAPTURE_OUT_DIR": str(shots), "CAPTURE_NO_GATE": "0"})
    adv, camp = name_parts(li["order_name"])
    base = {"kind": "deck", "order_id": li["order_id"], "line_item_id": li["line_item_id"],
            "order_name": li["order_name"], "advertiser": adv,
            "campaign": camp, "start_date": li.get("start"), "end_date": li.get("end")}
    if p.returncode != 0 or not (shots / "manifest.json").exists():
        return {**base, "status": "failed", "note": (p.stderr or p.stdout)[-500:]}
    ae = json.loads((REPO / "settings.json").read_text()).get("ae_names", {})
    spec = sas.build_spec(shots, ae)
    if not spec["shots"]:
        return {**base, "status": "failed", "note": "no framed in-context shots"}
    (shots / "spec.json").write_text(json.dumps(spec, ensure_ascii=False))
    safe = "".join(c for c in spec["advertiser"] if c not in '/\\:*?"<>|')
    pptx = work / f"{safe} - {li['line_item_id']} - Proof of Placement.pptx"
    q = _run([sys.executable, "scripts/build_screenshots_deck.py",
              str(shots / "spec.json"), str(pptx)], {})
    if q.returncode != 0 or not pptx.exists():
        return {**base, "status": "failed", "note": (q.stderr or q.stdout)[-500:]}
    m = json.loads((shots / "manifest.json").read_text())
    note = f"Shot on {m.get('article_url', '')} · {len(spec['shots'])} slide(s)"
    return {**base, "file_name": pptx.name, "file_bytes": pptx.read_bytes(),
            "summary_md": note, "note": note, "status": "ok"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--kind", choices=("final", "deck"), required=True)
    ap.add_argument("--from", dest="frm", default=os.environ.get("CR_FROM") or "")
    ap.add_argument("--to", default=os.environ.get("CR_TO") or "")
    ap.add_argument("--orders", default=os.environ.get("CR_ORDERS") or "")
    ap.add_argument("--line-items", default=os.environ.get("CR_LINE_ITEMS") or "")
    ap.add_argument("--force", action="store_true",
                    default=(os.environ.get("CR_FORCE") or "").lower() == "true")
    ap.add_argument("--max", type=int, default=25, help="cap per run (GAM + runner time)")
    a = ap.parse_args()

    today = datetime.now(ET).date()
    start, end = default_window(a.kind, today)
    if a.frm:
        start = date.fromisoformat(a.frm)
    if a.to:
        end = date.fromisoformat(a.to)
    client = _soap()
    e = engine()

    if a.kind == "final":
        if a.orders.strip():
            ids = [x.strip() for x in a.orders.replace(",", " ").split() if x.strip()]
            svc = client.GetService("OrderService", version=V)
            todo = [{"order_id": str(o.id), "order_name": o.name,
                     "start": _d(getattr(o, "startDateTime", None)),
                     "end": _d(getattr(o, "endDateTime", None))}
                    for o in _page(svc, "getOrdersByStatement", f"id IN ({', '.join(ids)})")]
        else:
            todo = ended_orders(client, start, end)
        keyf = lambda x: (x["order_id"], "")  # noqa: E731
        builder = build_final
    else:
        if a.line_items.strip():
            ids = [x.strip() for x in a.line_items.replace(",", " ").split() if x.strip()]
            li_svc = client.GetService("LineItemService", version=V)
            lis = _page(li_svc, "getLineItemsByStatement", f"id IN ({', '.join(ids)})")
            o_svc = client.GetService("OrderService", version=V)
            names = {str(o.id): o.name for o in _page(
                o_svc, "getOrdersByStatement",
                f"id IN ({', '.join(sorted({str(li.orderId) for li in lis}))})")} if lis else {}
            todo = [{"order_id": str(li.orderId), "order_name": names.get(str(li.orderId), ""),
                     "line_item_id": str(li.id), "line_item_name": li.name,
                     "start": _d(getattr(li, "startDateTime", None)),
                     "end": _d(getattr(li, "endDateTime", None))} for li in lis]
        else:
            todo = started_lines(client, start, end)
        keyf = lambda x: (x["order_id"], x["line_item_id"])  # noqa: E731
        builder = build_deck

    print(f"{a.kind}: window {start} → {end}, {len(todo)} candidate(s)")
    done = ok = 0
    for item in todo:
        oid, lid = keyf(item)
        if not a.force and already(e, a.kind, oid, lid):
            print(f"  skip {oid} {lid} — already built")
            continue
        if done >= a.max:
            print(f"  stopping at --max {a.max}; the rest go on the next run")
            break
        done += 1
        print(f"  build {a.kind} {oid} {lid} {item.get('order_name', '')[:90]}")
        with tempfile.TemporaryDirectory() as td:
            try:
                row = builder(item, Path(td))
            except Exception as ex:  # noqa: BLE001
                row = {"kind": a.kind, "order_id": oid, "line_item_id": lid,
                       "order_name": item.get("order_name"), "status": "failed",
                       "note": f"{type(ex).__name__}: {ex}"[:500]}
        store(e, row)
        ok += row["status"] == "ok"
        print(f"    -> {row['status']} {row.get('file_name') or row.get('note', '')[:200]}")
    print(f"{ok} of {done} built")
    return 0 if ok == done else 1


if __name__ == "__main__":
    raise SystemExit(main())
