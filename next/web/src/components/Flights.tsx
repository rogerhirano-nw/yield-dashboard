import type { Flight } from "../api";
import { isNum } from "../format";

const usd = (v: number | null | undefined, dp = 2) =>
  isNum(v) ? `$${v.toLocaleString("en-US", { minimumFractionDigits: dp, maximumFractionDigits: dp })}` : "—";
const k = (v: number | null | undefined) =>
  !isNum(v) ? "—" : v >= 1e6 ? `${(v / 1e6).toFixed(1)}M` : v >= 1e3 ? `${(v / 1e3).toFixed(1)}K` : `${Math.round(v)}`;
const md = (iso: string | null) => (iso ? new Date(iso + "T12:00:00").toLocaleDateString("en-US", { month: "short", day: "2-digit" }) : "");

/** Daily CPA with the goal: dashed goal line, red tint over the breach zone. */
function CpaSpark({ pts, goal, over, w = 300, h = 46, line = true, area = false }: {
  pts: [string, number][]; goal?: number; over?: boolean; w?: number; h?: number; line?: boolean; area?: boolean;
}) {
  if (pts.length < 2) return null;
  const vals = pts.map((p) => p[1]);
  const hi = Math.max(...vals, goal ?? 0) * 1.1 || 1;
  const x = (i: number) => 6 + (i / (pts.length - 1)) * (w - 12);
  const y = (v: number) => 4 + (1 - v / hi) * (h - 8);
  const d = vals.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)} ${y(v).toFixed(1)}`).join(" ");
  const col = over ? "var(--state-critical)" : goal ? "var(--state-positive-muted)" : "var(--text-secondary)";
  return (
    <svg className="spark" viewBox={`0 0 ${w} ${h}`} aria-hidden="true">
      {isNum(goal) && <rect x={0} y={0} width={w} height={y(goal)} fill="var(--state-critical-surface)" />}
      {isNum(goal) && <line x1={0} x2={w} y1={y(goal)} y2={y(goal)} stroke="var(--spark-ref)" strokeDasharray="3 3" />}
      {area && <path d={`${d} L${x(vals.length - 1)} ${h} L${x(0)} ${h} Z`} fill={col} fillOpacity={0.12} />}
      {line && <path d={d} fill="none" stroke={col} strokeWidth={1.8} />}
      <path d={`M${x(vals.length - 1)} ${y(vals[vals.length - 1])}h0`} stroke={col} strokeWidth={5} strokeLinecap="round" />
    </svg>
  );
}

function pctChip(v: number | null, invert = false) {
  if (!isNum(v)) return null;
  const good = invert ? v < 0 : v > 0;
  return <span className={`dlt ${good ? "up" : "down"}`}>{v > 0 ? "▲" : "▼"} {Math.abs(v).toFixed(1)}%</span>;
}

export function Flights({ flights }: { flights: Flight[] }) {
  if (!flights.length) return null;
  const goal = flights[0].goal;
  return (
    <section className="flights">
      <div className="eyebrow">Priority flights · daily monitor <span className="eyebrow-sub">{flights.length} betting flight{flights.length > 1 ? "s" : ""} · CPA goal ${goal.toFixed(0)}</span></div>
      {flights.map((f) => {
        const s = f.summary, gd = f.goal_delta;
        const empty = !s.impressions && !s.conversions;
        const cls = gd ? (gd.over ? "crit" : "pos") : "";
        const pctOfGoal = isNum(s.cpa) ? (s.cpa / f.goal) * 100 : null;
        const dates = s.date_min ? `${md(s.date_min)} – ${md(s.date_max)}, ${s.date_max?.slice(0, 4)}` : "";
        return (
          <details key={f.title} className={`flight ${cls}`}>
            <summary>
              <div className="fl-name"><b>{f.title}</b><small>{dates}{s.conversions ? ` · ${s.conversions.toLocaleString("en-US")} conv.` : ""}</small></div>
              {empty ? <div className="muted">No data</div> : <>
                <div className="fl-cpa"><small>CPA</small><b>{usd(s.cpa)}</b>
                  {gd && <span className={`pill ${gd.over ? "pill-red" : "pill-green"}`}>{gd.over ? `✗ ${usd(gd.delta)} over` : `✓ ${usd(gd.delta)} under`}</span>}</div>
                <div className="fl-stats">
                  <div><small>Conv.</small>{k(s.conversions)}</div>
                  <div><small>Spend</small>{usd(s.spend_usd)}</div>
                  <div><small>CVR</small>{isNum(s.conv_rate) ? `${s.conv_rate.toFixed(2)}%` : "—"}</div>
                  <div><small>% of goal</small><span className={gd?.over ? "txt-crit" : "txt-green"}>{isNum(pctOfGoal) ? `${pctOfGoal.toFixed(0)}%` : "—"}</span></div>
                </div>
                <div className="fl-spark"><CpaSpark pts={s.daily_cpa} goal={f.goal} over={gd?.over} /><small>Daily CPA · goal ${f.goal.toFixed(0)}</small></div>
                <span className="fl-more">View detail →</span>
              </>}
            </summary>
            {!empty && (
              <div className="fl-body">
                <div className="fl-hero">
                  <div><small>Cost per acquisition</small><b>{usd(s.cpa)}</b>
                    {isNum(s.delta_cpa) && <span className={`dlt ${s.delta_cpa < 0 ? "up" : "down"}`}>{s.delta_cpa > 0 ? "▲" : "▼"} {usd(Math.abs(s.delta_cpa))} vs prior period</span>}</div>
                  <div><small>Conversions</small><b>{s.conversions.toLocaleString("en-US")}</b>{pctChip(s.delta_conversions)}</div>
                  <div><small>Spend</small><b>{usd(s.spend_usd)}</b>{pctChip(s.delta_spend)}</div>
                  <div><small>Conv. rate</small><b>{isNum(s.conv_rate) ? `${s.conv_rate.toFixed(3)}%` : "—"}</b></div>
                  <div><small>Clicks</small><b>{s.clicks.toLocaleString("en-US")}</b></div>
                </div>
                <div className="fl-charts">
                  <div className="chart"><div className="chart-h">Daily conversions</div><CpaSpark pts={s.daily_conversions} area w={500} h={90} /></div>
                  <div className="chart"><div className="chart-h">Daily CPA · goal ${f.goal.toFixed(0)}</div><CpaSpark pts={s.daily_cpa} goal={f.goal} over={gd?.over} w={500} h={90} /></div>
                </div>
                {[["By ad size", s.by_ad_size, "ad_size"], ["By format", s.by_media_type, "media_type"]].map(([t, rows, key]) =>
                  (rows as Record<string, number | string | null>[]).length ? (
                    <table key={t as string} className="fl-table"><caption>{t as string}</caption>
                      <thead><tr><th></th><th>Impr.</th><th>Clicks</th><th>Conv.</th><th>Spend</th><th>CPA</th><th>CVR</th></tr></thead>
                      <tbody>{(rows as Record<string, number | string | null>[]).map((r) => (
                        <tr key={String(r[key as string])}><td>{String(r[key as string])}</td><td>{k(r.impressions as number)}</td><td>{k(r.clicks as number)}</td>
                          <td>{k(r.conversions as number)}</td><td>{usd(r.spend_usd as number)}</td><td>{usd(r.cpa as number)}</td>
                          <td>{isNum(r.conv_rate) ? `${(r.conv_rate as number).toFixed(2)}%` : "—"}</td></tr>))}</tbody>
                    </table>) : null)}
              </div>
            )}
          </details>
        );
      })}
    </section>
  );
}
