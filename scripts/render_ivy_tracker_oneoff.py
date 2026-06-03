"""
One-off renderer for Ivy's Campaign Tracker HTML, run via workflow_dispatch.
Queries gam_campaigns + gam_campaigns_hourly for the 6 LIs in GAM_HOURLY_LINE_ITEMS
and prints a self-contained HTML block to stdout (no email send).

The full HTML is wrapped between BEGIN_HTML / END_HTML markers so it can be
extracted from the workflow log and forwarded manually when the seller-comms
email path is unavailable (e.g., AgentMail daily quota exhausted).
"""

import os
import sys
import html
from datetime import date
import sqlalchemy
from sqlalchemy import text


def main() -> None:
    li_ids_csv = os.environ.get("GAM_HOURLY_LINE_ITEMS", "").strip()
    if not li_ids_csv:
        sys.exit("GAM_HOURLY_LINE_ITEMS env not set")
    li_ids = [x.strip() for x in li_ids_csv.split(",") if x.strip()]

    engine = sqlalchemy.create_engine(
        os.environ["DATABASE_URL"],
        pool_pre_ping=True,
    )
    with engine.connect() as conn:
        meta_rows = conn.execute(
            text(
                "SELECT line_item_id, line_item_name, order_name, status,"
                " line_item_type, cpm_rate, start_date, end_date,"
                " impressions_goal, lifetime_impressions_delivered"
                " FROM gam_campaigns"
                " WHERE line_item_id = ANY(:ids)"
                " ORDER BY order_name, line_item_name"
            ),
            {"ids": li_ids},
        ).mappings().all()

        hourly_rows = conn.execute(
            text(
                "SELECT line_item_id, date, hour, ad_server_impressions"
                " FROM gam_campaigns_hourly"
                " WHERE line_item_id = ANY(:ids)"
                "   AND date = (SELECT MAX(date) FROM gam_campaigns_hourly"
                "                WHERE line_item_id = ANY(:ids))"
                " ORDER BY line_item_id, hour"
            ),
            {"ids": li_ids},
        ).mappings().all()

    by_li_hourly: dict[str, list[dict]] = {}
    for r in hourly_rows:
        by_li_hourly.setdefault(str(r["line_item_id"]), []).append(dict(r))

    today_str = date.today().strftime("%a, %b %-d, %Y")

    out: list[str] = []
    out.append(
        "<div style='max-width:640px;margin:0 auto;padding:16px;"
        "font:14px/1.5 -apple-system,BlinkMacSystemFont,Segoe UI,Helvetica,Arial,sans-serif;color:#1a1a1a'>"
    )
    out.append(
        f"<div style='background:#0a0a0a;color:#fff;padding:20px 28px;border-radius:8px 8px 0 0'>"
        f"<div style='font-size:11px;font-weight:600;letter-spacing:1.5px;text-transform:uppercase;color:#9ca3af'>"
        f"Newsweek · Campaign Tracker (one-off)</div>"
        f"<div style='font-size:20px;font-weight:600;margin-top:8px'>Daily Performance Report</div>"
        f"<div style='color:#9ca3af;font-size:13px;margin-top:4px'>Ivy Lee · {today_str}</div>"
        "</div>"
    )
    out.append(
        "<div style='background:#fef3c7;padding:12px 28px;border:1px solid #fde68a;"
        "color:#78350f;font-size:12px'>"
        "Sent manually by ad-ops — AgentMail daily quota was exhausted at automated send time."
        "</div>"
    )
    for m in meta_rows:
        lid = str(m["line_item_id"])
        name = html.escape(m["line_item_name"] or "(unnamed)")
        order = html.escape(m["order_name"] or "")
        cpm = float(m["cpm_rate"] or 0)
        is_av = (
            "-AV-" in (m["line_item_name"] or "").upper()
            or "_AV_" in (m["line_item_name"] or "").upper()
            or cpm == 0.0
        )
        revenue_tag = (
            "<span style='display:inline-block;margin-left:8px;padding:2px 7px;"
            "background:#f0e6d2;color:#78350f;font-size:10px;font-weight:600;"
            "letter-spacing:0.04em;text-transform:uppercase;border-radius:3px'>Added value</span>"
            if is_av else ""
        )
        hrs = by_li_hourly.get(lid, [])
        total_today = sum(int(h["ad_server_impressions"] or 0) for h in hrs)
        out.append(
            "<div style='border:1px solid #e5e7eb;border-radius:6px;margin-top:16px;padding:14px 18px'>"
            f"<div style='font-weight:600'>{name}{revenue_tag}</div>"
            f"<div style='color:#6b7280;font-size:12px;margin-top:2px'>{order} · LI {lid} · "
            f"flight {m['start_date']} → {m['end_date']} · CPM ${cpm:.2f}</div>"
            "<div style='margin-top:12px;font-size:13px'>"
            f"<strong>Today: {total_today:,} imp</strong>"
            f" across {len(hrs)} hour{'s' if len(hrs) != 1 else ''}"
            "</div>"
        )
        if hrs:
            out.append("<table style='border-collapse:collapse;margin-top:8px;font-size:12px'>"
                       "<thead><tr style='color:#6b7280;text-align:left'>"
                       "<th style='padding:4px 12px 4px 0'>Hour (ET)</th>"
                       "<th style='padding:4px 0'>Impressions</th></tr></thead><tbody>")
            for h in hrs:
                hr = int(h["hour"])
                imp = int(h["ad_server_impressions"] or 0)
                out.append(
                    f"<tr><td style='padding:2px 12px 2px 0'>{hr:02d}:00</td>"
                    f"<td style='padding:2px 0'>{imp:,}</td></tr>"
                )
            out.append("</tbody></table>")
        else:
            out.append(
                "<div style='color:#9ca3af;font-size:12px;margin-top:6px'>"
                "No hourly delivery rows for today (LI may not be delivering or just started).</div>"
            )
        out.append("</div>")
    out.append(
        "<div style='color:#9ca3af;font-size:11px;margin-top:16px;text-align:center'>"
        "yield-dashboard · one-off manual render</div>"
        "</div>"
    )
    html_body = "".join(out)

    print("===BEGIN_HTML===")
    print(html_body)
    print("===END_HTML===")
    print(f"\nRendered {len(meta_rows)} LIs, {len(hourly_rows)} hourly rows.")


if __name__ == "__main__":
    main()
