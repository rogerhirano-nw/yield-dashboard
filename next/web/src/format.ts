export const dash = "—";
export const isNum = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);

export function money(v: number | null): string {
  if (!isNum(v)) return dash;
  if (Math.abs(v) >= 1e6) return `$${(v / 1e6).toFixed(2)}M`;
  if (Math.abs(v) >= 1e3) return `$${(v / 1e3).toFixed(1)}K`;
  return `$${v.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}
export function money0(v: number | null): string {
  return isNum(v) ? `$${Math.round(v).toLocaleString("en-US")}` : "$0";
}
export function count(v: number | null): string {
  if (!isNum(v)) return dash;
  if (v === 0) return "0";
  if (Math.abs(v) >= 1e6) return `${(v / 1e6).toFixed(2)}M`;
  if (Math.abs(v) >= 1e3) return `${(v / 1e3).toFixed(1)}K`;
  return Math.round(v).toLocaleString("en-US");
}
export function kmb(v: number | null): string {
  if (!isNum(v)) return dash;
  if (Math.abs(v) >= 1e6) return `${(v / 1e6).toFixed(1)}M`;
  if (Math.abs(v) >= 1e3) return `${(v / 1e3).toFixed(0)}K`;
  return Math.round(v).toLocaleString("en-US");
}
export function pct(v: number | null, dp = 1): string {
  return isNum(v) ? `${v.toFixed(dp)}%` : dash;
}
export function ivt(v: number | null): string {
  if (!isNum(v)) return dash;
  if (v === 0) return "0%";
  if (v < 1) return `${v.toFixed(2)}%`;
  if (v < 10) return `${v.toFixed(1)}%`;
  return `${v.toFixed(0)}%`;
}
export function shortDate(iso: string | null): string {
  if (!iso) return dash;
  return new Date(iso + "T12:00:00").toLocaleDateString("en-US", { month: "short", day: "numeric" });
}
export function ago(iso: string | null): string {
  if (!iso) return "";
  let s = iso.replace(" ", "T");
  if (!/[zZ]|[+-]\d\d:?\d\d$/.test(s)) s += "Z";
  const t = Date.parse(s);
  if (!Number.isFinite(t)) return "";
  const m = Math.round((Date.now() - t) / 60000);
  if (m < 2) return "just now";
  if (m < 60) return `${m} min ago`;
  if (m < 1440) return `${Math.round(m / 60)} h ago`;
  return `${Math.round(m / 1440)} d ago`;
}

/** Δ chip under a cell: dashboard_logic.classify_delta, ported. */
export function deltaChip(d: number | null, unit: string, lowerIsWorse = true) {
  if (!isNum(d) || Math.abs(d) < 0.05) return null;
  const improving = lowerIsWorse ? d > 0 : d < 0;
  const mag = Math.abs(d);
  const body = mag < 1 ? mag.toFixed(2) : mag.toFixed(1);
  return { text: `${d > 0 ? "▲" : "▼"} ${body}${unit}`, cls: improving ? "up" : "down" };
}
