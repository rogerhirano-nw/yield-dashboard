const PILLS: [string, string, string][] = [
  ["", "All flagged", "all"],
  ["ending", "Ending soon", "crit"],
  ["underpacing", "Underpacing", "crit"],
  ["overpacing", "Overpacing", "warn"],
  ["viewability", "Viewability", "info"],
];
export const TRIAGE_LABEL: Record<string, string> = Object.fromEntries(PILLS.map(([k, l]) => [k, l]));

export function Triage({ counts, total, active, onPick }: {
  counts: Record<string, number>; total: number; active: string; onPick: (k: string) => void;
}) {
  const flagged = counts.flagged ?? 0;
  return (
    <div className="triage">
      <span className="triage-label">Needs you today</span>
      <div className="triage-pills">
        {PILLS.map(([key, label, sev]) => {
          const n = key ? counts[key] ?? 0 : flagged;
          const on = active === key;
          return (
            <button key={key || "all"} type="button" className={`fpill sev-${sev}` + (on ? " on" : "")}
              disabled={!on && key !== "" && n === 0} aria-pressed={on} onClick={() => onPick(key)}>
              <span className="dot" />{label} <b>{n}</b>
            </button>
          );
        })}
      </div>
      <span className="triage-sum">
        {flagged ? <><b>{flagged}</b> of {total} need attention</> : "✓ All clear"}
      </span>
    </div>
  );
}
