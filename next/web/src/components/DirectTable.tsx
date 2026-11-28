import { useState } from "react";
import type { Band, Row } from "../api";
import { dash, deltaChip, ivt, isNum, money0, pct } from "../format";
import { Drawer } from "./Drawer";

export const PAGE_SIZE = 25;

function Delta({ d, unit, lowerIsWorse = true }: { d: number | null; unit: string; lowerIsWorse?: boolean }) {
  const c = deltaChip(d, unit, lowerIsWorse);
  return c ? <div className={`dlt ${c.cls}`}>{c.text}</div> : null;
}

const pill = (b: Band) => (b === "red" ? "pill-red" : b === "amber" || b === "over" ? "pill-amber" : b === "green" ? "pill-green" : "");

function Pace({ r, target }: { r: Row; target: number }) {
  const p = r.pacing_pct;
  if (!isNum(p)) return <span className="muted">{dash}</span>;
  const gap = r.pace_band === "over" ? { t: `${(p - target).toFixed(0)}pp over tgt`, c: "warn" }
    : r.pace_band === "red" || r.pace_band === "amber"
      ? { t: `${(target - p).toFixed(0)}pp below tgt`, c: r.pace_band === "red" ? "crit" : "warn" } : null;
  return (
    <>
      <span className={`pill ${pill(r.pace_band)}`}>{Math.round(p)}%</span>
      {gap && <div className={`gap ${gap.c}`}>{gap.t}</div>}
      {r.is_new ? <div className="dlt new">new line item</div>
        : isNum(r.pacing_prior) && <Delta d={p - r.pacing_prior} unit="pp" />}
    </>
  );
}

function Rate({ v, band, prior, dp = 1, target, showGap = false }: {
  v: number | null; band: Band; prior: number | null; dp?: number; target?: number | null; showGap?: boolean;
}) {
  if (!isNum(v)) return <span className="muted">{dash}</span>;
  const cls = band === "red" ? "pill pill-red" : band === "amber" ? "txt-amber" : band === "green" ? "txt-green" : "";
  const gap = showGap && isNum(target) && v < target && band !== "green"
    ? <div className={`gap ${band === "red" ? "crit" : "warn"}`}>{(target - v).toFixed(dp)}pp below tgt</div> : null;
  return (<><span className={cls}>{v.toFixed(dp)}%</span>{gap}{isNum(prior) && <Delta d={v - prior} unit="pp" />}</>);
}

function Attention({ v, band, prior }: { v: number | null; band: Band; prior: number | null }) {
  if (!isNum(v)) return <span className="muted">{dash}</span>;
  return (<><span className={band === "red" ? "pill pill-red" : band === "amber" ? "pill pill-amber" : ""}>{v.toFixed(0)}</span>
    {isNum(prior) && <Delta d={v - prior} unit="pp" />}</>);
}

function Ivt({ v, band, prior }: { v: number | null; band: Band; prior: number | null }) {
  if (!isNum(v)) return <span className="muted">{dash}</span>;
  const loud = band === "red" || band === "amber";
  return (<><span className={band === "red" ? "pill pill-red" : band === "amber" ? "pill pill-amber" : ""}>
    {loud ? `${v.toFixed(1)}%` : ivt(v)}</span>
    {isNum(prior) && <Delta d={v - prior} unit="pp" lowerIsWorse={false} />}</>);
}

function Progress({ p, band }: { p: number | null; band: Band }) {
  if (!isNum(p)) return null;
  const w = Math.max(0, Math.min(100, p * 100));
  const c = band === "red" ? "prog-red" : band === "amber" || band === "over" ? "prog-amber" : "prog-green";
  return (<div className="prog"><div className="prog-track"><div className={`prog-fill ${c}`} style={{ width: `${w}%` }} /></div>
    <span>{w.toFixed(0)}%</span></div>);
}

function subtitle(r: Row) {
  const f = r.ad_format || dash;
  return isNum(r.cpm_rate) ? `${f} · $${+r.cpm_rate.toPrecision(6)} CPM` : f;
}

export function DirectTable({ rows, page, onPage, target, networkId }: {
  rows: Row[]; page: number; onPage: (p: number) => void; target: number; networkId: string;
}) {
  const [open, setOpen] = useState<string | null>(null);
  const pages = Math.max(1, Math.ceil(rows.length / PAGE_SIZE));
  const cur = Math.min(Math.max(page, 0), pages - 1);
  const slice = rows.slice(cur * PAGE_SIZE, cur * PAGE_SIZE + PAGE_SIZE);
  const pager = (top: boolean) => pages > 1 && (
    <div className="pager">
      <button type="button" disabled={cur === 0} onClick={() => onPage(cur - 1)} aria-label="Previous page">‹</button>
      <div><b>Page {cur + 1} of {pages}</b>{top && <small>{slice.length} of {rows.length} shown</small>}</div>
      <button type="button" disabled={cur >= pages - 1} onClick={() => onPage(cur + 1)} aria-label="Next page">›</button>
    </div>
  );
  return (
    <>
      {pager(true)}
      <div className="tbl" role="table">
        <div className="tr th" role="row">
          {["Line item", "Revenue", "Delivered", "Pace", "Viewable", "Attention", "SIVT", "GIVT", "CTR", "VCR", "Seller", "Progress"]
            .map((h) => <div key={h} role="columnheader">{h}</div>)}
        </div>
        {slice.map((r) => {
          const isOpen = open === r.line_item_id;
          return (
            <div key={r.line_item_id} className={"rowwrap" + (isOpen ? " open" : "")}>
              <div className="tr" role="row" tabIndex={0} aria-expanded={isOpen}
                onClick={() => setOpen(isOpen ? null : r.line_item_id)}
                onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setOpen(isOpen ? null : r.line_item_id); } }}>
                <div className="c-name">
                  <span className="chev">›</span>
                  {r.ordinal ? <span className="li-ord">#{r.ordinal}</span> : null}
                  <div><div className="li-name">{r.display_name}</div><div className="li-sub">{subtitle(r)}</div></div>
                </div>
                <div className="num"><span className={isNum(r.revenue) && r.revenue > 10000 ? "bold-rev" : ""}>{money0(r.revenue)}</span><Delta d={r.rev_delta} unit="%" /></div>
                <div className="num">{isNum(r.lifetime_impressions_delivered) ? fmtDelivered(r.lifetime_impressions_delivered) : dash}<Delta d={r.imp_delta} unit="%" /></div>
                <div className="num"><Pace r={r} target={target} /></div>
                <div className="num"><Rate v={r.viewability} band={r.view_band} prior={r.view_prior} target={r.view_target} showGap /></div>
                <div className="num"><Attention v={r.attention} band={r.attention_band} prior={r.attention_prior} /></div>
                <div className="num"><Ivt v={r.sivt} band={r.sivt_band} prior={r.sivt_prior} /></div>
                <div className="num"><Ivt v={r.givt} band={r.givt_band} prior={r.givt_prior} /></div>
                <div className="num">{r.ctr_target ? <Rate v={r.ctr} band={r.ctr_band} prior={r.ctr_prior} dp={2} />
                  : isNum(r.ctr) ? <>{pct(r.ctr, 2)}{isNum(r.ctr_prior) && <Delta d={r.ctr - r.ctr_prior} unit="pp" />}</> : dash}</div>
                <div className="num">{r.is_video ? <Rate v={r.vcr_cell} band={r.vcr_band} prior={r.vcr_prior} /> : <span className="muted">{dash}</span>}</div>
                <div className="seller">{r.seller_ae || <span className="seller-prog">Prog.</span>}</div>
                <div><Progress p={r.progress} band={r.pace_band} /></div>
                {/* mobile card */}
                <div className="mcard">
                  <div className="m-name">{r.ordinal ? <span className="li-ord">#{r.ordinal}</span> : null}{r.display_name}<div className="li-sub">{subtitle(r)}</div></div>
                  <div className="m-right"><div>{money0(r.revenue)}</div><Pace r={r} target={target} /></div>
                  <div className="m-bar"><span>pace</span><div className="prog-track"><div className={`prog-fill ${r.pace_band === "red" ? "prog-red" : r.pace_band === "green" ? "prog-green" : "prog-amber"}`} style={{ width: `${Math.max(0, Math.min(100, r.pacing_pct || 0))}%` }} /></div></div>
                </div>
              </div>
              {isOpen && <Drawer row={r} target={target} networkId={networkId} />}
            </div>
          );
        })}
      </div>
      {pager(false)}
    </>
  );
}

function fmtDelivered(v: number) {
  if (v >= 1e6) return `${(v / 1e6).toFixed(2)}M`;
  if (v >= 1e3) return `${(v / 1e3).toFixed(1)}K`;
  return Math.round(v).toLocaleString("en-US");
}
