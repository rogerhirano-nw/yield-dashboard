import { useEffect, useRef, useState } from "react";
import type { Filters, Options } from "../api";

type Multi = "advertiser" | "format" | "status" | "team" | "manager";
const LABEL: Record<Multi, string> = { advertiser: "Advertiser", format: "Format", status: "Status", team: "Team", manager: "Account Manager" };

function MultiSelect({ label, options, value, onChange }: { label: string; options: string[]; value: string[]; onChange: (v: string[]) => void }) {
  return (
    <fieldset className="ms">
      <legend>{label}</legend>
      <div className="ms-opts">
        {options.map((o) => {
          const on = value.includes(o);
          return (<label key={o} className={on ? "on" : ""}>
            <input type="checkbox" checked={on} onChange={() => onChange(on ? value.filter((x) => x !== o) : [...value, o])} />{o}</label>);
        })}
      </div>
    </fieldset>
  );
}

const summary = (v: string[]) => (v.length === 1 ? v[0] : `${v[0]} +${v.length - 1}`);

export function FilterBar({ f, options, appliedStatus, onChange }: {
  f: Filters; options: Options; appliedStatus: string[]; onChange: (f: Filters) => void;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const h = (e: MouseEvent) => { if (open && ref.current && !ref.current.contains(e.target as Node)) setOpen(false); };
    document.addEventListener("mousedown", h);
    return () => document.removeEventListener("mousedown", h);
  }, [open]);
  const status = f.status ?? appliedStatus;
  const chips: [string, () => void][] = [];
  if (f.seller !== "All") chips.push([`Seller: ${f.seller}`, () => onChange({ ...f, seller: "All", page: 0 })]);
  (["advertiser", "format", "status", "team", "manager"] as Multi[]).forEach((k) => {
    const v = k === "status" ? status : f[k];
    if (v.length) chips.push([`${k === "manager" ? "Manager" : LABEL[k]}: ${summary(v)}`, () => onChange({ ...f, [k]: [], page: 0 })]);
  });
  const set = (k: Multi, v: string[]) => onChange({ ...f, [k]: v, page: 0 });
  return (
    <div className="filterbar" ref={ref}>
      <button type="button" className="fbtn" aria-expanded={open} onClick={() => setOpen(!open)}>
        ⚙ Filters{chips.length ? ` · ${chips.length}` : ""}
      </button>
      {chips.map(([t, clear]) => <button key={t} type="button" className="chip-x" onClick={clear}>{t} ✕</button>)}
      {open && (
        <div className="popover" role="dialog" aria-label="Filters">
          <label className="sel"><span>Seller</span>
            <select value={f.seller} onChange={(e) => onChange({ ...f, seller: e.target.value, page: 0 })}>
              {options.seller.map((s) => <option key={s}>{s}</option>)}
            </select></label>
          <MultiSelect label="Status" options={options.status} value={status} onChange={(v) => set("status", v)} />
          <MultiSelect label="Format" options={options.format} value={f.format} onChange={(v) => set("format", v)} />
          <MultiSelect label="Team" options={options.team} value={f.team} onChange={(v) => set("team", v)} />
          <MultiSelect label="Account Manager" options={options.manager} value={f.manager} onChange={(v) => set("manager", v)} />
          <MultiSelect label="Advertiser" options={options.advertiser} value={f.advertiser} onChange={(v) => set("advertiser", v)} />
          {chips.length > 0 && <button type="button" className="clear" onClick={() => onChange({ seller: "All", advertiser: [], format: [], status: [], team: [], manager: [], triage: f.triage, page: 0 })}>Clear all filters</button>}
        </div>
      )}
    </div>
  );
}
