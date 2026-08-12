# Cognizant AI Impact Summit — CTR optimization plan (IO1053)

**Campaign:** Cognizant AI Impact Summit Sponsorship (order `SO01053`, AE Ivy
Lee). Brand media flight 7/1–11/30/26; three branded articles (#1 4/28–5/28,
#2 6/2–7/2, #3 7/27–8/27) each with display + Apple News + paid LinkedIn
support. Client report (JC) flagged the campaign under benchmark 2026-08-12;
this doc is the diagnostic + action plan. Numbers below are GAM flight-to-date
(4/28 → 8/11 ET) from the one-off pull on PR #354
(`scripts/cognizant_media_perf_pull.py`, re-runnable via
`cognizant_media_perf.yml` workflow dispatch) unless marked as report-window
(7/1–8/2) figures.

**Benchmarks (from the client report):** NW Display 0.10% CTR · NW Video
0.60% · Paid LinkedIn Display 0.80% / Video 1.00% · Articles: 1:36 avg time /
40% scroll (B2B vertical: 0:50 / 25%) · article display banners 0.10% · Apple
News 0.20%.

## Where display CTR actually leaks (GAM)

Blended brand-media display is **0.090%** flight-to-date (2,481 clicks /
2.77M impr across the four display LIs). The split shows the shortfall is
concentrated, not uniform:

| Cut | Impr | Clicks | CTR |
|---|---|---|---|
| Smartphone | 2,219,548 | 2,265 | **0.102%** ✅ |
| Desktop + tablet | 536,058 | 210 | **0.039%** ❌ |
| 320x50 (all LIs) | 663,057 | 715 | **0.108%** ✅ |
| 300x50 | 664,863 | 610 | 0.092% |
| 300x250 | 1,193,077 | 1,045 | 0.088% |
| 728x90 | 158,572 | 69 | **0.044%** ❌ |
| 970x250 | 85,786 | 42 | **0.049%** ❌ |

- Mobile delivery is already **above** the 0.10% benchmark on every display
  LI (0.092–0.117% by LI). The desktop leaderboard sizes (728x90, 970x250)
  run at less than half benchmark on every LI, and desktop underperforms
  even at 300x250.
- **All 15 LIs serve creatives with "Evenly" rotation.** Each display LI has
  one creative per size, so on multi-size slots (mobile flex 300x250/320x50/
  300x50; desktop leaderboard 728x90/970x250) even rotation keeps serving
  the losing size instead of letting GAM shift to the winner.
- The **agency-supplied `4262300_AIB_Integrated Campaign_970x250`** creative
  on Contextual-Display is the single worst unit: 0.016% on 12.3k impr (the
  Newsweek-built 970x250 on AV-Display does 0.065%).
- **AV-Display (LI 7300490129) already recovered**: 0.109% and 0.114% the
  last two full weeks, 0.103% flight-to-date — proof the 0.10% bar is
  reachable with the current message when delivery skews mobile.

**Actions (display), in order of impact:**
1. **Skew delivery mobile.** Cap desktop+tablet at ~10% of remaining goal on
   the four display LIs (currently ~19%). Mobile at 0.102% vs desktop/tablet
   at 0.039% means every shifted impression is worth ~2.6× in clicks.
2. **Switch creative rotation Evenly → Optimized** on the display LIs
   (7298658370, 7300490129, 7296276630, 7298691352 + the article-support
   display LIs). Low risk, reversible in the UI; lets multi-size slots favor
   320x50/300x250 over 728x90/970x250 organically.
3. **Pause the agency 970x250 on Contextual-Display** (creative
   138564164202) and serve the Newsweek-built 970x250 there; or drop the
   970x250/728x90 sizes from rotation entirely if the client accepts a
   mobile-first plan (they're only ~9% of delivery at ~0.045%).
4. Keep the AV (added-value) display line weighted up — it's the
   above-benchmark pool and free CTR for the blend.

## Pre-roll: it's the audience, not the creative

| LI | CTR | VCR | Viewable |
|---|---|---|---|
| Custom-Audience-Contextual-PreRoll (7298701648) | **0.620%** ✅ | 62.6% | 89.5% |
| Contextual-PreRoll (7298658235) | 0.585% | 63.2% | 89.6% |
| Custom-Audience-PreRoll (7298654119) | **0.468%** ❌ | 63.3% | 88.0% |

Same :30 creative economics everywhere — VCR and viewability are within a
point across all three lines, so the CA-only line's gap is the *audience
pool served out of context*. Layering the same audience **with** contextual
(the 0.620% line) beats benchmark. Device split on the CA line: tablet
1.11%, phone 0.466%, desktop 0.289%.

**Actions (video):** shift remaining Custom-Audience-PreRoll goal into
Custom-Audience-Contextual-PreRoll (or add the contextual layer to the CA
line's targeting); deprioritize desktop video; blended video moves from
0.580% to ≥0.60% with that rebalance alone.

## Paid LinkedIn (brand Display + per-article promos)

Chronic laggard across the whole campaign: article promos ran 0.046% (#1
video), 0.037% (#2 video), 0.018% (#3 static) vs 0.80–1.00% benchmarks.
Satish's delivery summary (2026-08-12, campaigns now paused) explains why:

| Campaign | Planned | Spent | Impr (planned) | Clicks | CTR | CPM |
|---|---|---|---|---|---|---|
| Display (ends 11/30) | $10,000.00 | $273.36 | 395,649 (333,333) | 513 | 0.13% | **$0.69** |
| Article 3 (ends 8/27) | $2,812.50 | $132.68 | 463,455 (165,442) | 107 | 0.02% | **$0.29** |

Planned CPMs were $30 (Display) and $17 (Article 3); actuals came in 43–59×
cheaper. **US in-feed LinkedIn inventory against a B2B audience does not
clear at $0.29–0.69 CPM** — delivery at that price is the signature of the
LinkedIn Audience Network (off-platform apps/sites) and/or audience
expansion widening far past the TAL, with Consideration→Clicks +
max-delivery happily buying the cheapest impressions available. The
over-delivery and the sub-0.1% CTR are the same phenomenon: enormous cheap
reach nobody engages with. This is a fixable settings problem, not a
creative or budget problem.

**Relaunch checklist (both campaigns, before resuming spend):**
1. **LinkedIn Audience Network: OFF** (feed placements only).
2. **Audience expansion / predictive audience: OFF** so the TAL is actually
   the audience.
3. Verify geo = US only; pull the **demographics report** (member country,
   company, job function) for the flight so far to confirm who actually saw
   it — attach to the client narrative if asked about the 0.02%.
4. Bid strategy: manual CPC (or cost cap) rather than maximum delivery;
   expect real CPMs of $30–60 — that is what buying the actual TAL costs.
5. Layer TAL with job function/seniority only if the matched TAL is large
   enough to deliver (LinkedIn wants ≥~50k matched for stable delivery —
   check the matched-audience count; if it's small, that's another reason
   the algorithm wandered off-list).
6. Creative: static single-image needs a stat/question hook + explicit CTA
   for cold B2B feeds — worth an A/B, but it's step 6, not step 1; 0.02% is
   a delivery-quality number, not a creative number.

## Article #3 (Sovereignty Imperative) — Roger's questions answered

- **Is the $2,800 budget limiting scale/engagement? No.** The campaign
  delivered **2.8× its planned impressions on 4.7% of budget**. Scale
  over-delivered; engagement is what's missing, and that's a targeting-
  quality problem (above). After the fix, the remaining ~$2,680 buys
  ~45–90k genuine in-feed TAL impressions ≈ 300–600 clicks at
  LinkedIn-normal CTRs — a far better client story than 463k impressions /
  107 clicks.
- **Would adding audience segments help CTR? Not as an addition.** Adding
  segments broadens delivery and dilutes CTR further — the on-site data
  shows the same shape (CA-only pre-roll 0.468% vs CA+contextual 0.620%).
  Keep the TAL as the core, verify expansion is off, and *narrow* with job
  function/seniority. If more scale is genuinely wanted, run a lookalike as
  a **separate** campaign so its CTR reads separately and can't drag the
  TAL line.
- **The TAL itself is the right ABM targeting** — it just has to actually
  be what's served. At $0.29 CPM it almost certainly wasn't.
- **The article content is working**: 1:36 avg time / 48% scroll beats the
  B2B benchmarks (0:50 / 25%) and is the best of the three articles. Page
  views (311 in week 1) follow from amplification volume — Apple News is
  the proven qualified driver (0.28% CTR on 191k impr for #3; 0.28–0.33%
  across all three articles vs 0.20% benchmark) and the on-site 300x250s
  run ~0.10–0.12%. Weight future article-promo dollars accordingly.

## Actualization guidance (Satish's question)

- **Article 3:** don't actualize-and-close yet. The flight runs to 8/27 —
  relaunch with the checklist above and let the remaining ~$2,680 buy real
  TAL engagement, then actualize August actuals at month end. If the client
  wants it closed instead, actualize the $132.68 in August; but closing now
  locks in the 0.02% CTR on the report.
- **Display (ends 11/30):** the *impression* goal is met but the campaign
  is being graded on CTR — resuming as-is buys more junk impressions and
  pushes CTR further down, so **do not "run a few more days for more
  clicks" unchanged**. Keep it paused until the settings are fixed, then
  re-pace the remaining ~$9,727 evenly through 11/30 at real in-feed CPMs
  (~$2.4k/mo ≈ 40–80k quality impressions/mo). Actualize monthly actuals
  rather than spreading already-delivered impressions across future months
  — those impressions exist, but re-labeling them doesn't fix the metric
  the client is watching (finance treatment is ultimately Roger's call with
  the IO in view).

## Monitoring + reconciliation

- The Cognizant order is in the dashboard's Direct tab (Campaigns view) —
  CTR bands against the same 0.10%/0.60% benchmarks; the triage pills
  surface it when it breaches. For the per-size/per-device split, re-run
  `cognizant_media_perf.yml` (workflow dispatch; `ORDER_NEEDLE=cognizant`).
- Two report reconciliation items for JC/Satish:
  1. The media tab's "Paid LinkedIn Display" row shows 675 clicks / 204,607
     impr (0.33%) through 8/2, while LinkedIn platform reporting shows 513
     clicks / 395,649 impr (0.13%) through the 8/11 pause — clicks can't
     decrease, so the two rows measure different things (ad-server vs
     platform clicks, or different campaign sets). Pick one source of truth
     for the client report.
  2. The media tab's "Paid LinkedIn Video" row (124,741 / 22) is the same
     buy as the article tab's Article-3 "LinkedIn Static" row — it's
     benchmarked at 1.00% (video) on one tab and belongs at 0.80% (display)
     on the other; fix the label so it isn't double-counted as two
     placements.

## Provenance

Client report `Cognizant_Branded_Article_Performance_Report_Article_1.xlsx`
(JC, windows 7/1–8/2 media / per-article article windows) + Satish's
LinkedIn delivery summary email (2026-08-12) + GAM one-off pull (PR #354
comment, 2026-04-28 → 2026-08-11 ET). GAM changes proposed here (rotation
switch, device reweight, creative pause, goal rebalance) were **not**
executed by the pull — it is read-only; execute via GAM UI or a scripted
follow-up on approval.
