# Handoff: Newsweek brand realignment — ad-revenue dashboard

## Overview
Re-skin the existing **Streamlit ad-revenue dashboard** so it reads as **Newsweek** without
touching its data logic. This is a **token + CSS refactor**, not a rebuild: swap colors, type,
spacing, and radius to the Newsweek system while keeping every chart, table, filter, and
severity calculation exactly as-is.

The flagship target is **View 1 — Campaigns**. All other views (By site/size, By DSP, Magnite,
Pubmatic, OpenSincera, Configure) reuse the same building blocks, so once Campaigns is converted
the rest inherit.

## About the design files
The files in this bundle are **design references created in HTML/CSS** — they show the intended
look, not production code to paste wholesale. Your job is to apply the **token block and component
CSS** (`newsweek-dashboard.css`) inside the **existing Streamlit app's** styling layer, mapping the
provided classes onto the dashboard's current markup (custom HTML components and Streamlit natives).
Recreate the *look* the references demonstrate using the app's established structure.

- `newsweek-dashboard.css` — **the deliverable to install.** A `:root` token block + ~200 lines of
  component CSS. This is what you wire in.
- `Dashboard Brand Audit.html` + `audit.css` — the visual rationale: a Before/After of the Campaigns
  view (identical markup, only tokens differ) plus a findings matrix. Reference only.

## Fidelity
**High-fidelity.** Colors, type, spacing, and radius are final. Match them exactly. The numbers and
copy in the references are placeholders — keep the dashboard's real data.

## Constraints
1. **Theme is light — Newsweek Paper.** Unpin `base="dark"` in `.streamlit/config.toml` (set `base="light"`).
   Newsweek is a light, print-rooted brand: the app canvas is warm **Paper `#FEFCF6`** with **Ink
   `#1F1E19`** text — see `--surface-*` / `--text-*`. *(If dark ever has to return, a warm-ink dark ramp
   is provided, commented, at the bottom of `newsweek-dashboard.css` — re-declare only those tokens in a
   dark scope; nothing else changes.)*
2. **Red is split into two roles, never mixed.**
   - `--brand-red` (`#E91D0C`) / `--brand-red-strong` (`#C41608`) = **chrome only**: the eyebrow tick,
     the active-tab underline, the logo/mark. **Never** on a metric, delta, sparkline, or chart series.
   - `--state-critical` (`#C41608`) = **data severity (breach)** only. It lives in the data plane
     (banded cells, banners, breach pills) and pairs with `--state-warning` / `--state-positive`.
   - Acceptance rule: *if a red pixel is not the mark, a tab, or a breach, it's a bug.*

## Implementation tasks (do in order)
1. **Set the canvas to light.** In `.streamlit/config.toml` set `base="light"` (unpin dark). Then
   **install fonts** — copy the Newsweek binaries — `BentonModDisp-Regular/Bold/Black.otf` and
   `FranklinGothic.ttf` + `FranklinGothicDemi.ttf` (from the Newsweek design system `/assets/fonts`)
   — into the app's static dir, and point the `@font-face` `url()`s at the top of
   `newsweek-dashboard.css` to them. If the licensed fonts can't be self-hosted, the fallback stacks
   (`Georgia` for display, system sans for UI) apply automatically — flag this to the user.
2. **Inject the CSS.** Load `newsweek-dashboard.css` once, early, via
   `st.markdown("<style>…</style>", unsafe_allow_html=True)` (read the file in) or serve it statically.
   It must load before the view renders.
3. **Re-point existing custom CSS** to the token variables — replace every hard-coded hex/px in the
   current `<style>` block with the matching `var(--…)`. Start with the Campaigns view.
4. **Map the component classes** (below) onto the existing markup. Where a block is a Streamlit native
   (e.g. `st.dataframe`), wrap it or target its `[data-testid]` and apply the surface/text/border tokens.
5. **Swap chart colors.** Set the line/bar/Altair category sequence to `--viz-1 … --viz-6`. Remove any
   Streamlit-red (`#FF4B4B`) from chart series, buttons, and links.
6. **Tabular numerals everywhere.** Apply `font-feature-settings: var(--num-feature)` to every figure —
   KPI numbers, table cells, deltas, axis labels.
7. **Run the red audit** from the acceptance rule above across all 7 views.
8. **Apply the Campaigns-view structural changes** (next section) — these are layout edits, not just tokens.

## Structural redesign — Campaigns view (do alongside the token pass)
The live review surfaced three layout problems beyond color/type. Fix all three; the reference build
is `Campaigns Full Redesign (compact).html` (desktop) + `Campaigns Mobile.html` (phone).

1. **Collapse the “Needs you today” band into a triage filter strip.** Today it’s a 4-column band where
   only the first column has content — three-quarters empty. Replace it with a row of filter pills
   (`.nw-triage` / `.nw-fpill`): All flagged / Ending soon / Underpacing / Overpacing / Viewability,
   each with a count + severity dot. Clicking a pill **filters the Direct campaigns table below** (set
   the table’s existing filter state) rather than rendering a separate list. One canonical table, no
   duplicated rows.
2. **Give the KPI strip a lead metric.** All nine tiles are equal weight, so the Priority-Flight CPA
   numbers out-read the page totals. Make **Revenue** the lead tile (`.nw-tile--lead`: brand-red top
   rule, 30px serif number); keep the other eight at 23px. Page totals must out-rank the monitor.

   > **⚠ KNOWN REGRESSION (live build, Jun 23) — fix the KPI cards first.** The deployed
   > "Overall performance" view renders the four top cards (Revenue / Paid Impressions / Avg eCPM /
   > Active Deals) as ~150px tall rounded boxes with the number floating at top-left and a large empty
   > void below. Three things are wrong and must be corrected:
   > 1. **Height** — they're wrapped in `st.container(border=True)` (or `st.metric` in a bordered
   >    container), which forces the tall box + dead space. **Render the `.nw-tile` markup as raw HTML
   >    instead** (`st.markdown(..., unsafe_allow_html=True)`). Tiles must hug content (~90px). The CSS
   >    now has a defensive `min-height:0` guard, but the real fix is dropping the container wrapper.
   > 2. **Card treatment** — they show a full rounded-box border (the "container + shadow" pattern the
   >    system explicitly avoids). Use the editorial **2px ink top-rule**, near-square `--radius-sm`
   >    corners, no full box.
   > 3. **Even fill** — every tile MUST carry a `.nw-tile__tgt` sub-line. Right now Avg eCPM and Revenue
   >    have one but **Paid Impressions has none**, so the row reads ragged. Give Paid Impressions a
   >    context line (e.g. "vs 3.0M last week") to match.
3. **Collapse the two Priority-Flight panels into a compact monitor.** Two full detail panels in the
   middle of the page bury the table and shout louder than the totals. Replace with one slim row per
   flight (`.nw-flight`): name · CPA + goal pill · 4 key stats · breach-shaded daily-CPA sparkline ·
   “View detail →”. Move the full breakdown (both charts, gauge, by-size/by-format tables) behind that
   link — its own view or an expander. The goal pill and the chart’s shaded over-goal zone make “is
   this flight hitting its $150 CPA goal?” readable at a glance. CPA cells in the breakdown tables use
   `.nw-cpa-band--over/--ok` (banded against the goal).

**Daily-monitor data note (not styling):** the two flights run different date windows (Jun 03–21 vs
Jun 01–21). For a true side-by-side, normalize both to the same window or label the mismatch.

**Distance-to-threshold subtext (Direct campaigns table).** Breaching banded cells carry a small
gap annotation under the value — Pace "12pp below tgt", Viewable "0.9pp below tgt" — the same idea
as PMP's "$X below floor". Show it only on non-healthy cells (`.cell .gap` / quantify vs the metric's
target). Healthy cells stay clean.

## Structural redesign — PMP deals section (same patterns, programmatic-specific)
The PMP (Programmatic) section reuses the same building blocks; reference build is `PMP Deals Redesign.html`.
The one new idea is **eCPM banded against the rate floor** (the programmatic equivalent of CPA-vs-goal).

1. **eCPM is the lead KPI.** For programmatic, yield is the headline — make Avg eCPM the `.nw-tile--lead`
   tile and surface the floor ($14.00) in its subtitle.
2. **Band eCPM cells against the floor** (`.nw-ecpm` + `.nw-ecpm__band--above/--near/--below`): at/above
   floor = healthy (no fill), within 10% = warning tint, below floor = critical tint, with a small
   “$X below floor” annotation. This makes the money-leaking deals scannable at a glance.
3. **Deal-type pills** (`.nw-tpill--pg/--pd/--pa`) replace bare gray TYPE text, with a small key.
4. **Floor banner** uses `.nw-banner--warning` and names the worst offender + dollars left on the table.
5. **PMP signals box**: the “No delivery” row takes `.nw-signal--crit` (critical tint) since dead deals
   are the actionable item; “spend momentum” splits gaining/losing green/red.

The same eCPM-floor banding extends to the other programmatic views (By DSP, Magnite, Pubmatic).

### PMP signals drill-downs (Spend momentum / No delivery)
These two expandable signals were already well-structured — apply the **token pass** (serif figures,
tabular numerals, severity reds split from brand red, warm hairlines) rather than a restructure.
Reference: `PMP Signals Redesign.html`. Two specific notes:
- **Band inactivity age in 3 severity steps** in the "No delivery" dead-deal rows: long (>~180d) =
  critical, mid (~30–180d) = warning, recent (<~30d) = muted. The live app shows them all in one red,
  so the most-stale deals don't stand out — the banding fixes that.
- **Copy (app logic, flag to user):** "ACTIVE · 503d inactive" reads as a contradiction. Prefer
  "Enabled · 503d no spend" or similar — the deal is enabled but not delivering.

## Mobile
Streamlit’s native `st.dataframe`/columns collapse on their own, but the **custom-HTML** blocks need
the `@media (max-width:700px)` rules in `newsweek-dashboard.css`:
- KPI strip → 2-up grid, Revenue lead spans full width.
- Tabs + triage pills → horizontal swipe rows (`overflow-x:auto`), not wrapping.
- Priority-Flight rows → stack into cards.
- Signals drill-downs → deal/seller rows stack; the open deal stacks ID + yield + full-width charts + 2-up stat grid; dead-deal rows carry the inactivity-age severity rail on the left edge.
- **Direct campaigns table → one condensed card per line item** (Revenue/Pace/Progress up front, rest
  behind an “All 9 metrics” expander). Do **not** horizontal-scroll an 11-column table on a phone.
  This likely needs custom HTML rendering rather than a native `st.dataframe` — flag the added effort.

### ⚠ Mobile regressions observed in the live build (Jun 23, 6:14pm) — fix these
The font + branding landed on mobile, but three things still need fixing:
1. **KPI cards render as giant empty boxes**, worst on tiles with no sparkline (AVG ECPM, ACTIVE
   DEALS): the value sits top-left with a large empty void below. Cause: the tiles stretch to match
   their tallest grid sibling and aren't hugging content. **Fixed in CSS** via
   `@media(max-width:700px){ .nw-kpis{align-items:start} .nw-tile,.kpi-tile{min-height:0;height:auto;align-self:start} }`.
   If your KPI class isn't `.nw-tile`/`.kpi-tile`, add it to that selector. Verify there is no
   explicit `height`/`min-height` on the tile or its Streamlit column wrapper.
2. **Dense secondary tables overflow** (BY AD SIZE / BY FORMAT — the CVR column is clipped). Wrap each
   in `<div class="nw-subtable-wrap">…</div>` so it scrolls horizontally instead of clipping. (This is
   the ONE place horizontal-scroll is acceptable — these are dense reference tables, not the main list.)
3. The black **“Manage app”** pill is Streamlit Cloud's owner-only deploy toolbar, NOT part of the
   design — ignore it; it won't show for viewers.

## Component spec → class map
Map each existing piece to the class in `newsweek-dashboard.css`. Exact values live in the CSS; the
table below is the wiring guide.

| Dashboard element | Class | Key rules |
|---|---|---|
| Eyebrow / section label | `.nw-eyebrow` | uppercase, 0.08em, semibold, brand-red text + 8px tick |
| Page header (22px) | `.nw-h1` | Benton Modern Display serif, 22px/1.15, sentence case |
| Header subtitle | `.nw-sub` | Franklin Gothic 13px, `--text-secondary` |
| Severity banner | `.nw-banner` + `--critical/--warning/--positive` | 8px radius, tinted surface, 3px left state bar |
| KPI tile (9-up) | `.nw-tile` (grid `.nw-kpis`) | serif number, top 2px rule, eyebrow label, target subtitle |
| Tile sparkline | `.nw-tile__spark polyline` | **neutral** stroke `--text-secondary`, never state color |
| Tile delta | `.nw-tile__delta.up/.dn` | green up / state-red down |
| Direct/PMP grid | `.nw-grid` / `.nw-grid__head` / `.nw-grid__row` | 1px warm hairlines, hover `--surface-2` |
| Benchmark-banded cell | `.nw-band--good/--warn/--bad` | tint surface + saturated text, 4px radius |
| Status pill | `.nw-pill--good/--warn/--bad` | pill radius, uppercase, dot marker |
| Ordinal / rank pill | `.nw-rank` | 20px pill, `--surface-2` |
| Tab row | `.nw-tabs` / `.nw-tab` | active tab → 3px brand-red underline (chrome) |
| Section divider | `.nw-rule` | 2px `--border-strong` |
| Triage filter strip | `.nw-triage` / `.nw-fpill` (+ `--all/--crit/--warn/--info`) | filter pills that scope the table; count + severity dot |
| KPI lead tile | `.nw-tile--lead` | Revenue anchor — brand-red top rule, 30px serif |
| Priority-Flight monitor row | `.nw-flight` (+ `--crit/--pos`) | slim row: name · CPA · goal pill · stats · sparkline · detail link |
| Flight goal pill | `.nw-flight__goal--crit/--pos` | over/under $150 CPA goal |
| Goal-banded CPA cell | `.nw-cpa-band--over/--ok` | CPA tinted red when over goal |
| eCPM-vs-floor cell | `.nw-ecpm` + `.nw-ecpm__band--above/--near/--below` | eCPM tinted by distance to rate floor |
| Deal-type pill | `.nw-tpill--pg/--pd/--pa` | programmatic deal-type tag |
| PMP signal row | `.nw-signal` (+ `--crit`) | spend-momentum / no-delivery; crit tint on dead deals |

## Design tokens
All values are defined as CSS variables in `newsweek-dashboard.css` (`:root`). Summary:

- **Brand chrome:** `--brand-red #e91d0c`, `--brand-red-strong #c41608`
- **Surfaces (light):** `--surface-0 #fefcf6` (Paper), `--surface-1 #ffffff`, `--surface-2 #f6f2e6`,
  `--border #e7e0c9`, `--border-strong #1f1e19`
- **Text:** `--text-primary #1f1e19` (Ink), `--text-secondary #57564f`, `--text-muted #8c887b`
- **State (light):** positive `#3c6b14`, warning `#8a6d00`, critical `#c41608` (+ `…-surface` pale rgba tints)
- **Data-viz:** `--viz-1 #4b62e0`, `#2d8d92`, `#824dd7`, `#d84f86`, `#b08900`, `#5f9e2a`
- **Type:** `--font-display "Benton Modern Display"`, `--font-sans "Franklin Gothic"`;
  eyebrow tracking `0.08em`; tabular numerals `"tnum" 1`
- **Spacing (4px base):** `--space-1 4` … `--space-6 24`
- **Radius:** `--radius-sm 4` (tiles/cells), `--radius-md 8` (banners), `--radius-pill 999`

## Severity thresholds
**Do not change the thresholds.** Keep the existing green/amber/red banding logic (IVT bands, pacing
%, viewability, etc.) exactly. Only the **colors** change — point each band at the `--state-*`
tokens (re-toned for a light canvas: darker state text on pale tints). The grammar (green=healthy,
amber=warn, red=breach) is preserved.

## Notes
- Streamlit hot-swaps classnames (`st-emotion-cache-*`); prefer `[data-testid]` selectors or wrap
  blocks in your own `.nw-*` containers rather than targeting generated classes.
- The **Configure** view is utilitarian and lowest priority — apply surface/text/border tokens only.
- Keep all interaction/behavior as-is; the token pass is visual-only. The Campaigns-view structural
  changes (triage filter wiring, monitor drilldown, mobile cards) DO touch layout/state — see that section.

## Files
- `newsweek-dashboard.css` — token block + component CSS to install (includes Campaigns-view layout + mobile rules)
- `Campaigns Full Redesign (compact).html` — **canonical desktop reference** for the assembled Campaigns view
- `Campaigns Mobile.html` — phone reflow reference (annotated)
- `PMP Deals Redesign.html` — PMP / programmatic section reference (eCPM-vs-floor banding)
- `PMP Deals Mobile.html` — PMP deals table, phone reflow (eCPM-vs-floor card per deal)
- `PMP Signals Redesign.html` — expanded Spend-momentum / No-delivery drill-downs (token pass + age banding)
- `PMP Signals Mobile.html` — signals drill-downs, phone reflow (severity-rail dead-deal rows)
- `Dashboard Brand Audit.html` — Before/After + token findings (reference)
- `audit.css` — styles for the audit doc (reference only; not for the app)
