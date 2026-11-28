import type { Kpi } from "../api";
import { count, ivt, money, pct } from "../format";
import { Spark } from "./Spark";

function value(k: Kpi): string {
  switch (k.key) {
    case "revenue": return money(k.value);
    case "impressions": return count(k.value);
    case "attention": return k.value == null ? "—" : k.value.toFixed(0);
    case "sivt": case "givt": return ivt(k.value);
    case "ctr": return pct(k.value, 2);
    default: return pct(k.value, 1);
  }
}

export function KpiStrip({ kpis }: { kpis: Kpi[] }) {
  return (
    <section aria-label="Today's totals">
      <div className="eyebrow">Today's totals</div>
      <div className="kpi-row">
        {kpis.map((k) => (
          <div key={k.key} className={"tile" + (k.lead ? " tile--lead" : "")}>
            <div className="tile-label">{k.label}</div>
            <div className="tile-value">{value(k)}</div>
            {k.delta && <div className={`kpi-delta kpi-delta-${k.delta.cls}`}>{k.delta.text}</div>}
            <Spark values={k.series} target={k.target} />
            <div className="tile-sub">{k.sub}</div>
          </div>
        ))}
      </div>
    </section>
  );
}
