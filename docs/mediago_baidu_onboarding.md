# Baidu / MediaGo — programmatic onboarding (technical questionnaire)

> **Compiled 2026-06-22.** Partner: **Baidu MediaGo** (MediaGo = Baidu's global
> ad platform), onboarding as a **programmatic demand partner**.
> **Integration path: Magnite Demand Manager (managed Prebid) — server-side /
> Prebid Server (PBS)** (confirmed by Roger, 2026-06-22).
> MediaGo ships a standard Prebid adapter (`mediago`, client + server), so this
> is likely *enabling an existing bidder*, not a custom build — confirm w/ Magnite.
> Answers flagged *(Magnite)* are properties of Magnite's PBS, not Newsweek —
> route to the Magnite DM integration contact (see confirm-list at bottom).

## Ownership map (read first)
The questionnaire mixes three owners:
- **Magnite (PBS)** — RTB-protocol internals: OpenRTB version, gzip, loss URL,
  `tmax`/latency, QPS + throttling, `schain`, no-bid handling. Magnite's Prebid
  Server **is** the exchange; **Newsweek does not operate an RTB endpoint** (the
  `yield-dashboard` repo only knows Magnite as a *reporting* source).
- **Newsweek** — brand safety, fraud, creative governance, privacy/consent,
  supply chain, test campaign, inventory.
- **MediaGo** — bidder behaviour, ads.txt seat (if any), creative compliance.

---

## A. Protocol & integration  *(Magnite/PBS confirms exact build)*
| Question | Answer |
|---|---|
| OpenRTB versions | **2.5** (PBS core; 2.6 fields supported) |
| Native API | **N/A** — Display + Video; **1.2** if native enabled |
| Auction type | **First-price** |
| Gzipped bid requests | **Yes** — PBS per-bidder (`endpointCompression`) |
| Loss URL (`lurl`) | **Not fired by default** in PBS |
| Integration docs | Standard **Prebid + OpenRTB 2.5** spec; S2S specifics from **Magnite**; MediaGo's existing `mediago` adapter applies |
| Test campaign | **Yes** — US · web (desktop+mobile) · Display 300×250 first (then 728×90 / 320×50 / 970×250) then VAST preroll · HTML5 + VAST · advertiser landing page |

## B. Infrastructure & traffic
| Question | Answer |
|---|---|
| Data-center locations | Magnite **PBS regions** — **US East primary** (US-majority audience); Magnite confirms full list. *(Client-side path: requests originate from end-user browsers.)* |
| Multiple DCs → different traffic/partners | **Region/latency-based** routing, not partner-partitioned — *(Magnite)* |
| Countries >2M daily requests | **US** near-certain; likely **UK**, possibly **CA/AU/IN**. Exact list = GAM Country dimension or Magnite geo report |
| Required response latency | **`tmax` ~300 ms** typical S2S (range 250–500) — *(Magnite confirms configured value)* |
| Max QPS to MediaGo | **Ceiling ~2,000–3,000 QPS peak** at full eligibility / 100% allocation (derived: ~78M avg ad-requests/day → ~900–1,070 QPS avg × peak factor). Start capped low + ramp; final cap set in Magnite DM |
| QPS setup/management | Magnite DM traffic allocation + shaping *(Magnite)* |
| QPS throttling dimensions | geo / site / format / device / ad-size commonly supported; timeout-rate / bid-rate dynamic throttling = advanced — *(Magnite confirms which)* |
| Allowlist / blocklist controls | **Yes** — Publisher, Domain, GEO, Format, Device, Advertiser domain, Creative ID (GAM + Confiant + Prebid) |

## C. Privacy & consent  *(verified live on newsweek.com, 2026-06-22 — homepage + article page)*
| Question | Answer |
|---|---|
| GDPR consent format | **Both** — `regs.ext.gdpr` (applies flag) + `user.ext.consent` (TCF v2 string); complementary, not either/or |
| Pass GDPR signals? | **Yes** — TCF v2 CMP live (`__tcfapi` verified). EU/EEA only; US carries `gdpr=0` |
| COPPA | **N/A** — general-audience news; `regs.coppa=0`; no child-directed traffic |
| US Privacy / CCPA | **Yes** — legacy IAB **US Privacy string** (`regs.ext.us_privacy`; verified `__uspapi` → `1---`) |
| GPP | **No — not implemented** (verified: no `__gpp` on homepage or article pages). On legacy USP only. **→ action item** |

## D. Billing & impression counting  *(PBS server-side)*
| Question | Answer |
|---|---|
| No-bid HTTP code | **204 No Content** (preferred; PBS short-circuits). 200-with-empty-`BidResponse` tolerated |
| 200 no-bid body | Valid `BidResponse`: **`id` echoing request id** (required), `seatbid` empty/omitted, optional `nbr` reason code. Empty 200 body = parse error (counts as bidder error) |
| Billing / impression method | **`adm` + `burl`** — markup inline, `burl` fired at render. `nurl` = win notice only (overcounts; not for billing) |
| PBS specifics | **`burl` fired client-side at render (Prebid.js / Prebid SDK), NOT by PBS.** GAM makes the final render decision → **no server-to-server impression callback**; the `burl` call comes from the end-user device. **Reconcile on render, not win.** |
| Impression expiration window | bid **`exp` / `ttl`** — ~**300 s display**, up to **3600 s video**; not rendered in-window → expired/unbilled. *(Magnite confirms PBS/cache cap)* |
| Counting methodology docs | Standard **Prebid + MRC** (render-based, MRC-aligned). No proprietary Newsweek doc. Refs: docs.prebid.org (Prebid.js + Prebid Server), IAB OpenRTB 2.5/2.6 spec, MRC measurement guidelines, Magnite PBS docs |

## E. Brand safety, fraud & creative governance  *(Newsweek-owned)*
| Question | Answer |
|---|---|
| Restricted / prohibited categories | Set in **GAM** (blocked categories/advertisers + Protections) + **Confiant**; signalled via OpenRTB **`bcat` / `badv` / `battr`**. Prohibited: illegal, adult, counterfeit, malware/deceptive/auto-redirect, hate/shocking, fraudulent. Regulated (gambling, alcohol, pharma, crypto, political) **accepted but controlled** (vetted/direct). Binding list = **GAM export** |
| Fraud prevention / vendors | **DoubleVerify** (MRC-accredited IVT [SIVT+GIVT] + viewability + attention, per-line daily) · **Confiant** (malvertising, daily auto-blocking) · **GAM** (MRC-accredited Ad Traffic Quality) · **Magnite** (TAG-certified) · supply-chain transparency. **Measured: 99.3% valid, SIVT 0.36% / GIVT 0.36%, total IVT <0.72%** (DV, 7 d, 2,056 LIs / 113 M imps) |
| Creative appeal / re-review | **RevOps** (revops@newsweek.com) + Confiant re-scan → next daily blocklist sync (**~1 business day**). GAM blocks = ad-ops policy review; ad-server disapproval = GAM appeal. Existing weekly per-SSP Confiant outreach is the feedback channel. *No formal published SLA — set one (action item)* |
| Creative submission / approval | **No manual pre-approval for RTB** — creatives real-time via `adm`, governed by upfront `bcat`/`badv`/`battr` + automated scanning (GAM / Confiant / DV). Test campaign = integration-time validation. PG / direct / custom high-impact formats = GAM trafficking + ad-ops review |
| Ad quality / audit policy link | **No dedicated page.** Published: /privacy-policy, /cookie-policy, /terms-service, /terms-sale, /corrections. Quality program = DV / Confiant / GAM / MRC-TAG + ads.txt / sellers.json. **→ action: produce a one-page policy** |

## F. Supply chain  *(ads.txt verified 2026-06-22)*
- **Owner-operated, self-managed**: `OWNERDOMAIN=newsweek.com`, **no `MANAGERDOMAIN`** (no master reseller).
- 58 seller domains, **67 DIRECT / 118 RESELLER** — mixed (normal at scale).
- **MediaGo's path is DIRECT**: Magnite holds **1 DIRECT seat** (+23 reseller). Bid requests carry **`schain` (`complete=1`)** from PBS; Newsweek declared in Magnite's **sellers.json**. *(Magnite confirms `seller_id` / nodes.)*
- **MediaGo not yet in ads.txt** → add their authorization line(s) at go-live **if** they run their own seller seat (else covered by Magnite's seat + sellers.json). **→ action item**

---

## Verified findings & evidence (2026-06-22)
- **ads.txt** (newsweek.com/ads.txt): 187 lines, **67 DIRECT / 118 RESELLER**, 58 sellers; `OWNERDOMAIN=newsweek.com`, no `MANAGERDOMAIN`; Magnite (rubiconproject) 1 DIRECT / 23 RESELLER; Google DIRECT; Mobkoi DIRECT; **MediaGo absent**.
- **Request volume** (`magnite_site_daily`, last 7 d): ~65–92 M ad requests/day (avg ~78 M) → ~900–1,070 QPS avg, **~2–3 k QPS peak ceiling**. *(Do NOT quote the 9–15 B/day `bid_requests` fan-out — that's the sum across ~130 demand seats; MediaGo ≈ the ad-request rate.)*
- **Full-screen inventory**: 183 direct LIs / 3 PMP deals (~5 % each) — **custom/direct, not open-auction**. MediaGo full-screen = a PMP/PD deal conversation.
- **Verticals running** (programmatic): Multi 21, Gambling 8, Tech 11, Finance 6, Health 4, Pharma 1, Telco 3, Auto 2, Lifestyle/Food/Career 1 each.
- **Consent APIs** (homepage + article page, headless probe): `__tcfapi` ✅ · `__uspapi` ✅ (`1---`) · `__gpp` ❌.
- **DV IVT** (last 7 d): 2,056 LIs, 113.3 M monitored, **99.28 % valid, SIVT 0.358 %, GIVT 0.359 %**.
- **Policy pages**: /privacy-policy, /cookie-policy, /terms-service, /terms-sale, /corrections. **No ad-quality page.**

## Newsweek action items
1. **GPP enablement** — turn on GPP in the CMP + the **Prebid GPP module** (`regs.gpp` / `regs.gpp_sid`). Currently legacy USP-only; IAB has deprecated the standalone US Privacy string. *(RevOps + CMP + Magnite)*
2. **ads.txt authorization for MediaGo** — add their line(s) if they run their own seller seat; else covered by Magnite's seat + sellers.json. *(RevOps/ad-ops; MediaGo supplies the line)*
3. **Creative-appeal SLA** — confirm RevOps owns MediaGo appeals directly vs. via Magnite; set a named contact + turnaround. *(RevOps)*
4. **GAM blocklist export** — export blocked categories + advertiser domains; confirm the `bcat`/`badv` Magnite passes. This is the binding "restricted categories" attachment. *(ad-ops + Magnite)*
5. **Advertising Quality & Verification policy page** — one-page public asset summarizing DV / Confiant / MRC-TAG + transparency + appeals; reusable for every demand partner. *(RevOps)*
6. *(Optional)* **Per-country >2M request pull** — GAM Country dimension or Magnite geo report. *(pull on request)*

## Magnite Demand Manager confirm-list
Hand to the Magnite DM integration/solutions contact:
- OpenRTB version their PBS build sends (2.5 + which 2.6 fields)
- gzip (`endpointCompression`) on the MediaGo endpoint
- Loss URL / `lurl` firing behaviour
- Configured `tmax` (latency budget to MediaGo)
- QPS allocation/cap setup + which throttling dimensions are exposed
- `schain` nodes + Newsweek `seller_id` in sellers.json
- Whether MediaGo rides Magnite's existing seat or needs its own ads.txt authorization
- TCF version forwarded (v2.2?) + confirm both GDPR fields reach MediaGo
- **Prebid GPP module** enablement (for the GPP action item)
- 204 / 200 no-bid handling + `burl` render-firing on their PBS build
- Whether the `mediago` Prebid Server adapter can simply be enabled

---
*Compiled from the 2026-06-22 onboarding working session. Verified data points
were pulled live (prod cache via Supabase, newsweek.com ads.txt + consent APIs).
Anything marked (Magnite) is the operator's to confirm; this doc is the Newsweek
side of the answer set.*
