import { useCallback, useEffect, useState } from "react";
import { filtersFromUrl, filtersToQuery, getCampaigns, type CampaignsResponse, type Filters } from "./api";
import { DirectTable, PAGE_SIZE } from "./components/DirectTable";
import { FilterBar } from "./components/FilterBar";
import { Flights } from "./components/Flights";
import { KpiStrip } from "./components/KpiStrip";
import { TRIAGE_LABEL, Triage } from "./components/Triage";
import { ago } from "./format";

// Views not ported yet open the current (Streamlit) dashboard.
const OLD = "https://adops.azurewebsites.net";
const TABS: [string, string][] = [
  ["campaigns", "Campaigns"], ["site", "By site / size"], ["dsp", "By DSP"], ["magnite", "Magnite deals"],
  ["pubmatic", "Pubmatic deals"], ["opensincera", "OpenSincera"], ["tools", "Tools"],
];

export default function App() {
  const [f, setF] = useState<Filters>(filtersFromUrl);
  const [data, setData] = useState<CampaignsResponse | null>(null);
  const [err, setErr] = useState("");
  const [loading, setLoading] = useState(true);

  const update = useCallback((next: Filters) => {
    setF(next);
    const q = filtersToQuery(next);
    window.history.replaceState(null, "", q ? `?${q}` : window.location.pathname);
  }, []);

  useEffect(() => {
    let live = true;
    setLoading(true);
    getCampaigns(f).then((d) => { if (live) { setData(d); setErr(""); } })
      .catch((e) => live && setErr(String(e))).finally(() => live && setLoading(false));
    return () => { live = false; };
    // the page number never changes the data
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [f.seller, f.triage, f.advertiser.join("|"), f.format.join("|"), f.team.join("|"), f.manager.join("|"), (f.status ?? ["<default>"]).join("|")]);

  const rows = data?.rows ?? [];
  const pages = Math.max(1, Math.ceil(rows.length / PAGE_SIZE));
  return (
    <div className="app">
      <header className="mast">
        <div className="eyebrow red">Yield &amp; pacing</div>
        <h1>Overall performance</h1>
        <div className="clock">{new Date().toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit", timeZone: "America/New_York" })} ET</div>
      </header>
      <nav className="tabs">
        {TABS.map(([k, l]) => k === "campaigns"
          ? <a key={k} className="tab on" href="?">{l}</a>
          : <a key={k} className="tab" href={`${OLD}/?view=${k}`}>{l}</a>)}
      </nav>
      {err && <div className="banner sev-red">Couldn't load campaigns: {err}</div>}
      {!data && !err && <div className="loading">Loading…</div>}
      {data && (
        <main className={loading ? "busy" : ""}>
          <div className="toolbar">
            <FilterBar f={f} options={data.options} appliedStatus={data.applied_status} onChange={update} />
            <span className="fresh">Updated {ago(data.pulled_at)} · {data.total.toLocaleString("en-US")} line items</span>
          </div>
          <Triage counts={data.triage} total={data.total} active={f.triage} onPick={(t) => update({ ...f, triage: t, page: 0 })} />
          <KpiStrip kpis={data.kpis} />
          <Flights flights={data.flights} />
          <section className="direct">
            <div className="eyebrow">All direct campaigns</div>
            <h2>Direct campaigns <small>
              · {f.triage ? `${TRIAGE_LABEL[f.triage]} · ${rows.length} of ${data.total}` : rows.length} line item{rows.length === 1 ? "" : "s"} · sorted A–Z
              {pages > 1 ? ` · page ${Math.min(f.page, pages - 1) + 1}/${pages}` : ""}</small></h2>
            <div className="legend"><span className="lg crit" />under <span className="lg warn" />off-target <span className="lg ok" />healthy <span>— = N/A</span></div>
            {rows.length ? <DirectTable rows={rows} page={f.page} onPage={(p) => update({ ...f, page: p })} target={data.pacing_target} networkId={data.gam_network_id} />
              : <div className="empty">No campaigns found for the selected filters.</div>}
          </section>
        </main>
      )}
    </div>
  );
}
