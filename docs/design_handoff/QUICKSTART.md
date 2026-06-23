# QUICKSTART — read this first

You are updating an existing **Streamlit** ad-revenue dashboard to the Newsweek brand.
Previous attempts regressed because the spec was prose. **This time, use the provided code verbatim.**

## ⚠ FONTS & ICONS — the two things still missing in the live build

**Fonts weren't loading** because the design system's `fonts.css` uses relative paths
(`url("../assets/fonts/…")`) that don't resolve inside a deployed Streamlit app — so every
`@font-face` fails and the page falls back to Georgia/system sans. **Fix:** use the provided
`newsweek-fonts-embedded.css` (base64-embedded Benton Modern Display + Franklin Gothic — no paths, no
static serving). `inject_brand()` now loads it automatically. Just copy the file in alongside the others.

**Icons were missing** because the system ships no icon font and Streamlit's `st.markdown` won't run
an external `<script>` (so a Lucide CDN tag does nothing). **Fix:** `newsweek_icons.py` provides
inline-SVG Lucide glyphs:
```python
from newsweek_icons import icon
st.markdown(f'{icon("filter")} Filters · 1', unsafe_allow_html=True)
# inside markup: f'<span class="nw-fpill">{icon("clock",14)} Ending soon</span>'
```
Color inherits via `currentColor`; size/stroke are args. Available: filter, settings, chevron-right,
chevron-down, x, arrow-up, arrow-down, external, alert, clock, download, search.

Required files in the app dir: `newsweek-fonts-embedded.css`, `newsweek-dashboard.css`,
`newsweek_components.py`, `newsweek_icons.py`.

## Missing section titles + spacing
Several views render without their title. Put a header at the top of EVERY view:
```python
from newsweek_components import section_header, block_label
section_header("Overall performance", kicker="Yield & Pacing", meta="5:06 PM EDT · 39 line items")
# sub-sections within a view:
block_label("Priority Flights")
block_label("Direct campaigns", count="39 line items")
```
The CSS (`newsweek-dashboard.css` §5) also tightens Streamlit's loose default stack into a deliberate
rhythm (`stVerticalBlock` gap, block-container padding, KPI margins) and pins KPI sparklines to the
tile bottom so the wide 4-up cards stop reading as empty boxes. No layout code change needed — it's
all in the injected CSS once `inject_brand()` runs.

## The one rule that fixes the KPI cards
**Never** render the KPI tiles with `st.metric` or wrap them in `st.container(border=True)`.
That is exactly what produced the tall empty rounded boxes. Instead render the markup with
`st.markdown(..., unsafe_allow_html=True)` via the helper in `newsweek_components.py`.

## 3 steps
1. Copy `newsweek-dashboard.css` and `newsweek_components.py` into the app directory.
2. At the top of the app, once:
   ```python
   import streamlit as st
   from newsweek_components import inject_brand, kpi_strip, triage_strip, flight_row, band
   st.set_page_config(layout="wide")
   inject_brand()          # loads fonts + CSS — MUST be first
   ```
3. Replace each block with its helper. The "Overall performance" KPI row becomes:
   ```python
   kpi_strip([
     {"label":"Revenue","value":"$63.9K","tgt":"▲ 14.3% vs 7-day avg","delta":14.3,"lead":True},
     {"label":"Paid Impressions","value":"34.31M","tgt":"vs 30.7M last week","delta":11.9},
     {"label":"Avg eCPM","value":"$1.86","tgt":"target $1.50","delta":4.0},
     {"label":"Active Deals","value":"312","tgt":"PD 41 · PA 188 · PMP 83"},
   ])
   ```
   That single call produces: Revenue as the **lead tile** (red top-rule, 30px serif),
   all four with a 2px ink top-rule, near-square corners, no full box, even sub-lines.

## Acceptance check (the "red audit")
After each view, scan for these — every one is a regression to fix:
- [ ] No KPI card taller than its content (~90px). No empty void below the number.
- [ ] No full rounded-box borders on KPI tiles — top-rule only.
- [ ] Revenue is visibly the largest/lead metric.
- [ ] Every KPI tile has a sub-line (no ragged/empty tiles).
- [ ] Red appears ONLY as: brand accents, the lead-tile rule, and critical-state text/bands.
      Positive = green, warning = amber. Headlines/labels never red.
- [ ] Numbers use tabular figures (already in CSS via font-feature-settings).

## What each file is
| File | Use |
|---|---|
| `newsweek-dashboard.css` | the tokens + all `.nw-*` component styles — inject once |
| `newsweek_components.py` | **drop-in Streamlit render functions** — call these, don't reinvent |
| `README.md` | full spec, structural changes, mobile rules, severity thresholds |
| `*Redesign.html` / `*Mobile.html` | visual references — what the output must look like |
| `Dashboard Brand Audit.html` | before/after rationale |

## Two decisions left to the product owner (don't guess)
- **Fonts** are licensed — self-host the Benton Modern Display + Franklin Gothic files, or it falls
  back to Georgia / system sans.
- **Copy:** "ACTIVE · 503d inactive" reads as a contradiction → prefer "Enabled · 503d no spend".

---
**Suggested prompt to start Claude Code:**
> Read `QUICKSTART.md` then `README.md` in this folder. Copy `newsweek-dashboard.css` and
> `newsweek_components.py` into the Streamlit app. Call `inject_brand()` once at the top, then replace
> the "Overall performance" KPI row using `kpi_strip(...)` exactly as shown — do NOT use st.metric or
> st.container(border=True). Then run the acceptance check in QUICKSTART against every view.
