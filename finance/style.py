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

FUND_COLORS = {
    "iShares Physical Gold ETC":        "#58a6ff",
    "iShares Core MSCI World (Acc)":    "#1f6feb",
    "iShares Developed World":          "#3fb950",
    "AMUNDI INDEX MSCI World AE Dis":   "#2ea043",
    "AMUNDI INDEX S&P 500 ESG AE Acc":  "#26a641",
    "INDEX S&P 500 ESG AE Acc EUR":     "#1a7f37",
    "INDEX MSCI World AE Dis EUR":      "#116329",
    "ROBECO BP Global Premium EQ D":    "#56d364",
    "GLOBAL PREMIUM EQ D Acc EUR":      "#6bc8f5",
    "BBVA Investment Funds":            "#ffa657",
    "Other Investment":                 "#8b949e",
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
