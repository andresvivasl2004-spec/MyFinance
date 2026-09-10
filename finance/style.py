"""
style.py — Dark-theme colour palette & matplotlib global style for spending analysis.
"""

import matplotlib.pyplot as plt

# ── Base colours ──────────────────────────────────────────────────────────────
DARK_BG  = "#0d1117"
PANEL_BG = "#161b22"
GRID_COL = "#30363d"
TEXT_COL = "#c9d1d9"

ACCENT1 = "#58a6ff"   # blue  — headers
ACCENT3 = "#f78166"   # coral — average lines / highlights
ACCENT5 = "#e3b341"   # amber — dining bars

# ── Per-category colour map ────────────────────────────────────────────────────
CAT_COLORS = {
    "Rent":           "#6e40c9",
    "Investments":    "#1f6feb",
    "Education":      "#388bfd",
    "Entertainment":  "#a371f7",
    "Dining & Food":  "#e3b341",
    "Groceries":      "#3fb950",
    "Electricity":    "#f0e68c",
    "Gas":            "#ffa657",
    "Transport":      "#79c0ff",
    "Health & Beauty":"#ff7b72",
    "Shopping":       "#c9b458",
    "Gym":            "#56d364",
    "Personal Care":  "#d2a8ff",
    "Water":          "#58a6ff",
    "Phone":          "#8b949e",
    "Social & Gifts": "#f9826c",
    "Fees & Tax":     "#484f58",
    "Other":          "#30363d",
}

# ── Investment colours ─────────────────────────────────────────────────────────
ACCOUNT_COLORS = {
    "Trade Republic": "#58a6ff",
    "MyInvestor":     "#3fb950",
    "BBVA":           "#ffa657",
    "Unknown":        "#8b949e",
}

# ── Asset class colours ─────────────────────────────────────────────────────────
ASSET_CLASS_COLORS = {
    "Equity":       "#58a6ff",
    "Fixed income": "#3fb950",
    "Commodities":  "#e3b341",
    "Unspecified":  "#8b949e",
}

# A fund donut can have any two slices land next to each other depending on
# that period's amounts (they're sorted by size, not by a fixed position), so
# this needs colors that stay distinguishable in any pairing — not just
# adjacent ones. The previous palette was six near-identical greens plus
# three near-identical blues, which made most funds impossible to tell apart
# at a glance (worse still for colorblind readers). These 8 hues are the
# dataviz skill's validated dark-mode categorical set, assigned in its fixed
# order — confirmed via scripts/validate_palette.js against this app's own
# panel background (#161b22): every adjacent pair clears the colorblind and
# normal-vision separation floors. (Full all-pairs separation isn't
# achievable past 3 categorical hues on any palette — see the skill's
# palette.md — so identity still leans on the legend's direct fund-name +
# amount labels, never on hue alone, exactly as the skill recommends.)
FUND_COLORS = {
    "Physical Gold ETC":         "#3987e5",   # blue
    "Global Equity ETF (Acc)":   "#d95926",   # orange
    "Developed World Index":     "#199e70",   # aqua
    "World Index Fund Dis EUR":  "#c98500",   # yellow
    "Sustainable 500 Index Acc": "#d55181",   # magenta
    "Value Equity Fund D":       "#008300",   # green
    "Bank Managed Funds":        "#9085e9",   # violet
    "Other Investment":          "#e66767",   # red
}


def apply_dark_theme():
    """Apply the dark matplotlib theme globally. Call once at notebook start."""
    plt.rcParams.update({
        "figure.facecolor": DARK_BG,
        "axes.facecolor":   PANEL_BG,
        "axes.edgecolor":   GRID_COL,
        "axes.labelcolor":  TEXT_COL,
        "axes.titlecolor":  TEXT_COL,
        "xtick.color":      TEXT_COL,
        "ytick.color":      TEXT_COL,
        "grid.color":       GRID_COL,
        "text.color":       TEXT_COL,
        "legend.facecolor": PANEL_BG,
        "legend.edgecolor": GRID_COL,
    })
    print("✅ Dark theme applied.")
