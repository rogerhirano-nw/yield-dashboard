# Newsweek brand fonts — drop-in directory

The dashboard's `.streamlit/config.toml` declares `[[theme.fontFaces]]`
entries pointing at this directory (served by Streamlit static serving at
`/app/static/fonts/...`). The licensed binaries are **not** committed
(this directory is gitignored except for this README) — copy them here
from the Newsweek design system `/assets/fonts`:

| File | Family | Weight |
|---|---|---|
| `FranklinGothic.ttf` | Franklin Gothic (UI sans) | 400 |
| `FranklinGothicDemi.ttf` | Franklin Gothic (UI sans) | 600 |
| `BentonModDisp-Regular.otf` | Benton Modern Display (headers + KPI figures) | 400 |
| `BentonModDisp-Bold.otf` | Benton Modern Display | 700 |
| `BentonModDisp-Black.otf` | Benton Modern Display | 800 |

Until the files exist, the designed fallback stacks apply automatically:
**Georgia** for display/serif, **Helvetica Neue / Arial** for UI sans —
the app renders fine without them.

Licensing note: these are licensed faces. Confirm the web-embedding license
covers the Streamlit Cloud deployment before committing the binaries to a
repo (this repo is private, but the served font URLs are public to anyone
who can reach the app).
