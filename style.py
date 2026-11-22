"""
style.py — Dark-theme colour palette & matplotlib global style for spending analysis.
"""

import matplotlib.pyplot as plt

import private_data

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
    "Natural Gas":    "#ffa657",
    "Transport and Gasoline": "#79c0ff",
    "Travel":         "#db61a2",
    "Health and Personal Care": "#ff7b72",
    "Shopping":       "#c9b458",
    "Gym":            "#56d364",
    "Water":          "#58a6ff",
    "Phone":          "#8b949e",
    "Social & Gifts": "#f9826c",
    "Fees & Tax":     "#484f58",
    "Other":          "#30363d",
}

# Categories added in Settings are not in the map above, and painting them all
# one fallback grey makes two new categories indistinguishable in a stack. They
# take a colour from this reserve instead, chosen by a hash of the name so a
# category keeps the same colour across runs (and across machines) rather than
# shifting whenever the category list changes.
#
# These are not arbitrary: they were picked greedily to maximise the minimum
# OKLab distance from every colour already in use, so each is at least as well
# separated from its neighbours as the existing palette's own tightest pair.
EXTRA_CAT_COLORS = [
    "#c2410c",   # brick
    "#2dd4bf",   # teal
    "#bef264",   # lime
    "#9333ea",   # plum
    "#bc8cff",   # violet
    "#f778ba",   # magenta
]


def cat_color(name: str) -> str:
    """Colour for a category, whether or not it was known when this was written."""
    if name in CAT_COLORS:
        return CAT_COLORS[name]
    from zlib import crc32
    return EXTRA_CAT_COLORS[crc32(str(name).encode("utf-8")) % len(EXTRA_CAT_COLORS)]

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
_FUND_PALETTE = ["#3987e5", "#d95926", "#199e70", "#c98500",   # blue, orange, aqua, yellow
                 "#d55181", "#008300", "#9085e9"]             # magenta, green, violet
# One colour per fund, in the order they appear in private_data.json.
FUND_COLORS = dict(zip(private_data.load()["funds"], _FUND_PALETTE))
FUND_COLORS["Other Investment"] = "#e66767"   # red


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
