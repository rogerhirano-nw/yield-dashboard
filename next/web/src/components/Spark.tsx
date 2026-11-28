import { isNum } from "../format";

/** Uniform-scaling sparkline (viewBox 300×34, height auto) — the
 * Streamlit app's `_sparkline_svg(uniform=True)`: neutral line, dashed
 * target, round end dot. Never stretched, so the dot stays round. */
export function Spark({ values, target, w = 300, h = 34, color = "var(--text-secondary)" }: {
  values: (number | null)[] | null; target?: number | null; w?: number; h?: number; color?: string;
}) {
  const pts = (values || []).map((v, i) => [i, v] as const).filter(([, v]) => isNum(v)) as [number, number][];
  if (pts.length < 2) return null;
  const n = (values || []).length;
  const ys = pts.map(([, v]) => v).concat(isNum(target) ? [target] : []);
  let lo = Math.min(...ys), hi = Math.max(...ys);
  if (hi === lo) { hi += 1; lo -= 1; }
  const pad = 4, xp = 6;
  const x = (i: number) => xp + (i / Math.max(n - 1, 1)) * (w - 2 * xp);
  const y = (v: number) => pad + (1 - (v - lo) / (hi - lo)) * (h - 2 * pad);
  const d = pts.map(([i, v], k) => `${k ? "L" : "M"}${x(i).toFixed(1)} ${y(v).toFixed(1)}`).join(" ");
  const [li, lv] = pts[pts.length - 1];
  return (
    <svg className="spark" viewBox={`0 0 ${w} ${h}`} aria-hidden="true">
      {isNum(target) && <line x1={0} x2={w} y1={y(target)} y2={y(target)} stroke="var(--spark-ref)" strokeDasharray="3 3" strokeWidth={1} vectorEffect="non-scaling-stroke" />}
      <path d={d} fill="none" stroke={color} strokeWidth={1.6} strokeLinejoin="round" vectorEffect="non-scaling-stroke" />
      <path d={`M${x(li)} ${y(lv)}h0`} stroke={color} strokeWidth={5} strokeLinecap="round" vectorEffect="non-scaling-stroke" />
    </svg>
  );
}
