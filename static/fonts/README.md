# Newsweek fonts — drop-in directory

The dashboard's `@font-face` rules (top of the style block in
`dashboard.py`) point here via Streamlit static serving
(`/app/static/fonts/...`; `enableStaticServing = true` in
`.streamlit/config.toml`).

The Newsweek type binaries are **licensed and must not be committed**
(this directory is allowlisted in `.gitignore` for this README only).
Copy them from the Newsweek design system `/assets/fonts` export:

- `BentonModDisp-Regular.otf`
- `BentonModDisp-Bold.otf`
- `BentonModDisp-Black.otf`
- `FranklinGothic.ttf`
- `FranklinGothicDemi.ttf`

Until the files exist, the font requests 404 harmlessly and the CSS
falls back to Georgia (display serif) and the system sans stack — the
layout is identical, only the faces differ.

For the Streamlit Cloud deploy the binaries have to be present in the
deployed tree; since they can't live in the public repo, either vendor
them through a private submodule/secret-managed step or accept the
fallback stacks in production.
