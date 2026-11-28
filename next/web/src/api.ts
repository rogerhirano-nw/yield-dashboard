// Types + fetchers for the FastAPI backend (next/api). Same origin in prod;
// in dev, vite proxies /api to the backend (vite.config.ts).

export type Band = "red" | "amber" | "green" | "over" | null;

export interface Delta { delta: number; text: string; cls: string; basis?: string | null }

export interface Kpi {
  key: string; label: string; value: number | null; series: (number | null)[] | null;
  target: number | null; delta: Delta | null; sub: string | null; lead: boolean;
}

export interface Row {
  line_item_id: string; line_item_name: string; display_name: string; ordinal: number | null;
  order_id: string; order_name: string; advertiser: string | null; campaign_name: string | null;
  ad_format: string | null; cpm_rate: number | null; seller_ae: string | null; team: string | null;
  status: string | null; line_item_type: string | null; start_date: string | null; end_date: string | null;
  impressions_goal: number | null; lifetime_impressions_delivered: number | null; remaining: number | null;
  progress: number | null; revenue: number | null; rev_delta: number | null; imp_delta: number | null;
  pacing_pct: number | null; pacing_prior: number | null; pace_band: Band; is_new: boolean;
  viewability: number | null; view_prior: number | null; view_band: Band; view_target: number | null;
  attention: number | null; attention_prior: number | null; attention_band: Band;
  sivt: number | null; sivt_prior: number | null; sivt_band: Band;
  givt: number | null; givt_prior: number | null; givt_band: Band;
  ctr: number | null; ctr_prior: number | null; ctr_band: Band; ctr_target: number | null;
  is_video: boolean; vcr_cell: number | null; vcr_prior: number | null; vcr_band: Band; vcr_target: number | null;
  lifetime_clicks: number | null; _creative_max_dur: number | null; deal_id: string | null;
}

export interface Options {
  seller: string[]; advertiser: string[]; format: string[]; status: string[]; status_default: string[];
  team: string[]; manager: string[];
}

export interface FlightSummary {
  date_min: string | null; date_max: string | null; impressions: number; clicks: number; conversions: number;
  spend_usd: number; cpa: number | null; conv_rate: number | null;
  by_media_type: Record<string, number | string | null>[]; by_ad_size: Record<string, number | string | null>[];
  daily_conversions: [string, number][]; daily_cpa: [string, number][];
  delta_conversions: number | null; delta_cpa: number | null; delta_spend: number | null;
}
export interface Flight { title: string; goal: number; summary: FlightSummary; goal_delta: { over: boolean; delta: number } | null }

export interface CampaignsResponse {
  rows: Row[]; total: number; shown: number; kpis: Kpi[];
  triage: Record<string, number>; options: Options; applied_status: string[];
  pulled_at: string | null; pacing_target: number; gam_network_id: string; flights: Flight[];
}

export interface Detail {
  row: Row; dates: string[];
  series: Record<string, (number | null)[] | null>;
}

export interface Filters {
  seller: string; advertiser: string[]; format: string[]; status: string[] | null;
  team: string[]; manager: string[]; triage: string; page: number;
}

/** Filters <-> URL, so every view is a link (as in the Streamlit app). */
export function filtersFromUrl(): Filters {
  const q = new URLSearchParams(window.location.search);
  return {
    seller: q.get("seller") || "All",
    advertiser: q.getAll("advertiser"), format: q.getAll("format"),
    status: q.has("status") ? q.getAll("status").filter(Boolean) : null,
    team: q.getAll("team"), manager: q.getAll("manager"),
    triage: q.get("triage") || "", page: Number(q.get("page") || 0),
  };
}

export function filtersToQuery(f: Filters, withPage = true): string {
  const q = new URLSearchParams();
  if (f.seller && f.seller !== "All") q.set("seller", f.seller);
  for (const k of ["advertiser", "format", "team", "manager"] as const) f[k].forEach((v) => q.append(k, v));
  if (f.status !== null) {
    if (f.status.length === 0) q.append("status", "");
    f.status.forEach((v) => q.append("status", v));
  }
  if (f.triage) q.set("triage", f.triage);
  if (withPage && f.page) q.set("page", String(f.page));
  return q.toString();
}

export async function getCampaigns(f: Filters): Promise<CampaignsResponse> {
  const q = filtersToQuery(f, false);
  const r = await fetch(`/api/campaigns${q ? "?" + q : ""}`);
  if (!r.ok) throw new Error(`campaigns: HTTP ${r.status}`);
  return r.json();
}

export async function getDetail(id: string): Promise<Detail> {
  const r = await fetch(`/api/campaigns/${encodeURIComponent(id)}`);
  if (!r.ok) throw new Error(`detail: HTTP ${r.status}`);
  return r.json();
}
