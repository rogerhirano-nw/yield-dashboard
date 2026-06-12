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
- Keep all interaction/behavior as-is; this is purely a visual-token pass.

## Files
- `newsweek-dashboard.css` — token block + component CSS to install
- `Dashboard Brand Audit.html` — Before/After + findings (open in a browser; reference)
- `audit.css` — styles for the audit doc (reference only; not for the app)
