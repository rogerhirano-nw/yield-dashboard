import { useEffect, useState } from "react";
import { getDetail, type Detail, type Row } from "../api";
import { dash, isNum, kmb, shortDate } from "../format";
import { Spark } from "./Spark";

function banner(r: Row, target: number) {
  const p = r.pacing_pct;
  if (!isNum(p)) return null;
  const crit = target * 0.75, low = target * 0.9, high = target * 1.1;
  if (r.line_item_type === "SPONSORSHIP") return ["sev-ok", "✓ Sponsorship", "Pacing is 100% by definition for sponsorship lines."];
  if (p < crit) return ["sev-red", "⚠ Pacing critical", `${p.toFixed(1)}% pacing. Delivery well below expected.`];
  if (p < low) return ["sev-amber", "⚠ Underpacing", `${p.toFixed(1)}% pacing. Tracking behind expected pace.`];
  if (p <= high) return ["sev-ok", "✓ On track", `${p.toFixed(1)}% pacing.`];
  return ["sev-amber", "⚠ Overpacing", `${p.toFixed(1)}% pacing. Will exhaust goal before flight ends.`];
}

function flightBit(r: Row) {
  if (!r.start_date || !r.end_date) return "";
  const s = new Date(r.start_date + "T12:00:00"), e = new Date(r.end_date + "T12:00:00");
  const total = Math.max(Math.round((+e - +s) / 864e5), 1);
  const el = Math.min(Math.max(Math.round((Date.now() - +s) / 864e5), 0), total);
  return ` · day ${el} of ${total}`;
}

function DeliveryChart({ series, dates, r }: { series: (number | null)[]; dates: string[]; r: Row }) {
  const vals = series.map((v) => (isNum(v) ? v : 0));
  const W = 1000, H = 112, P = 10;
  let expected: number | null = null;
  if (isNum(r.impressions_goal) && r.start_date && r.end_date) {
    const days = Math.max(Math.round((+new Date(r.end_date) - +new Date(r.start_date)) / 864e5), 1);
    expected = r.impressions_goal / days;
  }
  const max = Math.max(...vals, 1) * 1.2;
  const x = (i: number) => P + (i / (vals.length - 1)) * (W - 2 * P);
  const y = (v: number) => H - P - (Math.min(v, max) / max) * (H - 2 * P);
  const mean = vals.reduce((a, b) => a + b, 0) / vals.length;
  const good = expected ? mean / expected >= 0.9 : true;
  const color = good ? "var(--state-positive-muted)" : "var(--state-warning)";
  const line = vals.map((v, i) => `${i ? "L" : "M"}${x(i)} ${y(v)}`).join(" ");
  return (
    <div className="chart">
      <div className="chart-h">7-day daily delivery <span>— actual{expected ? ` · - - expected ${kmb(expected)}/day` : ""}</span></div>
      <svg viewBox={`0 0 ${W} ${H}`}>
        <line x1={P} x2={W - P} y1={H - P} y2={H - P} stroke="var(--border)" />
        {expected && <line x1={P} x2={W - P} y1={y(expected)} y2={y(expected)} stroke="var(--spark-ref)" strokeDasharray="4 4" />}
        <path d={`${line} L${x(vals.length - 1)} ${H - P} L${x(0)} ${H - P} Z`} fill={color} fillOpacity={0.1} />
        <path d={line} fill="none" stroke={color} strokeWidth={2} />
        <path d={`M${x(vals.length - 1)} ${y(vals[vals.length - 1])}h0`} stroke={color} strokeWidth={7} strokeLinecap="round" />
      </svg>
      <div className="chart-dates">{dates.map((d, i) => <span key={d}>{new Date(d + "T12:00:00").toLocaleDateString("en-US", { weekday: "short", day: "numeric" })}{i === dates.length - 1 ? " · today" : ""}{expected && vals[i] < expected * 0.75 ? " ↓" : ""}</span>)}</div>
    </div>
  );
}

export function Drawer({ row: r, target, networkId }: { row: Row; target: number; networkId: string }) {
  const [d, setD] = useState<Detail | null>(null);
  const [err, setErr] = useState("");
  useEffect(() => { getDetail(r.line_item_id).then(setD).catch((e) => setErr(String(e))); }, [r.line_item_id]);
  const b = banner(r, target);
  const gamUrl = networkId ? `https://admanager.google.com/${networkId}#delivery/line_item/detail/line_item_id=${r.line_item_id}` : "";
  const smalls: [string, string, number | null, number][] = [
    ["Viewability", "viewability", r.view_target ?? 70, 1],
    ...(r.is_video ? [["VCR", "vcr", r.vcr_target, 1] as [string, string, number | null, number]] : []),
    ["CTR", "ctr", r.ctr_target, 2], ["Attention", "attention", 100, 0], ["SIVT", "sivt", 1, 2], ["GIVT", "givt", 1, 2],
  ];
  return (
    <div className="drawer">
      {b && <div className={`banner ${b[0]}`}><b>{b[1]}</b>{flightBit(r)}<div>{b[2]}</div></div>}
      {err && <div className="banner sev-red">{err}</div>}
      {d && (
        <div className="charts">
          {d.series.impressions && <DeliveryChart series={d.series.impressions} dates={d.dates} r={r} />}
          <div className={"sm-grid" + (smalls.length === 6 ? " sm-grid--6" : "")}>
            {smalls.map(([label, key, t, dp]) => {
              const s = d.series[key];
              const pts = (s || []).filter(isNum);
              if (pts.length < 2) return null;
              const last = pts[pts.length - 1];
              return (<div key={key} className="sm"><div className="sm-h">{label} <b>{key === "attention" ? last.toFixed(0) : `${last.toFixed(dp)}%`}</b></div>
                <Spark values={s} target={t} /></div>);
            })}
          </div>
        </div>
      )}
      <div className="li-card">
        <div className="li-head">
          <div><div className="eyebrow">Line item</div><div className="li-full">{r.line_item_name.replace(/^#\d+\s+/, "")}</div></div>
          {gamUrl && <a className="chip" href={gamUrl} target="_blank" rel="noreferrer">GAM ID · {r.line_item_id} ↗</a>}
        </div>
        <div className="li-hero">
          <div><span>Goal</span><b>{kmb(r.impressions_goal)}</b></div>
          <div><span>Delivered</span><b>{kmb(r.lifetime_impressions_delivered)}</b>
            {isNum(r.progress) && <div className="prog-track"><div className="prog-fill prog-green" style={{ width: `${r.progress * 100}%` }} /></div>}</div>
          <div><span>Remaining</span><b>{kmb(r.remaining)}</b></div>
        </div>
        <dl className="li-grid">
          <div><dt>Flight</dt><dd>{shortDate(r.start_date)} → {shortDate(r.end_date)}</dd></div>
          <div><dt>Format</dt><dd>{r.ad_format || dash}</dd></div>
          <div><dt>CPM</dt><dd>{isNum(r.cpm_rate) ? `$${+r.cpm_rate.toFixed(2)}` : dash}</dd></div>
          <div><dt>Revenue</dt><dd>{isNum(r.revenue) ? `$${Math.round(r.revenue).toLocaleString("en-US")}` : dash}</dd></div>
          <div><dt>Clicks</dt><dd>{isNum(r.lifetime_clicks) ? Math.round(r.lifetime_clicks).toLocaleString("en-US") : dash}</dd></div>
          <div><dt>Seller</dt><dd>{r.seller_ae || "Programmatic"}</dd></div>
          {r.is_video && isNum(r._creative_max_dur) && <div><dt>Creative duration</dt><dd>{r._creative_max_dur.toFixed(0)}s</dd></div>}
        </dl>
      </div>
    </div>
  );
}
