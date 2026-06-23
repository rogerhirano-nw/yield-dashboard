# newsweek_icons.py
# UI icons for the dashboard. The Newsweek system ships NO icon font, so per the
# design guide we substitute Lucide (clean line set that pairs with Franklin Gothic).
# Streamlit's st.markdown won't run external <script>, so we inline the SVGs.
# Each returns an inline <svg> string; size + color inherit from CSS (currentColor).
#
# Usage:
#   from newsweek_icons import icon
#   st.markdown(f'{icon("filter")} Filters', unsafe_allow_html=True)
#   # or inside your own markup:
#   f'<button class="nw-fpill">{icon("chevron-right",14)} Ending soon</button>'

_PATHS = {
    # name: inner SVG paths (Lucide, 24x24, stroke=currentColor)
    "filter":        '<path d="M22 3H2l8 9.46V19l4 2v-8.54L22 3z"/>',
    "settings":      '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/>',
    "chevron-right": '<path d="m9 18 6-6-6-6"/>',
    "chevron-down":  '<path d="m6 9 6 6 6-6"/>',
    "x":             '<path d="M18 6 6 18M6 6l12 12"/>',
    "arrow-up":      '<path d="m5 12 7-7 7 7M12 19V5"/>',
    "arrow-down":    '<path d="M12 5v14M5 12l7 7 7-7"/>',
    "external":      '<path d="M15 3h6v6M10 14 21 3M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/>',
    "alert":         '<path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3z"/><path d="M12 9v4M12 17h.01"/>',
    "clock":         '<circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/>',
    "download":      '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M7 10l5 5 5-5M12 15V3"/>',
    "search":        '<circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/>',
}

def icon(name, size=16, stroke=1.75, cls=""):
    """Return an inline Lucide SVG string. Color inherits via currentColor."""
    inner = _PATHS.get(name, "")
    c = f' class="{cls}"' if cls else ""
    return (
        f'<svg{c} xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" '
        f'viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="{stroke}" '
        f'stroke-linecap="round" stroke-linejoin="round" '
        f'style="display:inline-block;vertical-align:-0.18em">{inner}</svg>'
    )
