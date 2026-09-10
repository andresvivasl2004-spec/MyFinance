"""
charts.py — All matplotlib charts for the spending analysis.

Each function takes the processed data dict (output of processor.process())
and returns the matplotlib Figure, so you can display or save it from the notebook.
"""

import numpy as np
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import matplotlib.gridspec as gridspec
from datetime import datetime
from matplotlib.patches import Patch
from collections import defaultdict

from .style import (
    DARK_BG, PANEL_BG, GRID_COL, TEXT_COL,
    ACCENT3, ACCENT5,
    CAT_COLORS, ACCOUNT_COLORS, FUND_COLORS, ASSET_CLASS_COLORS,
)


def _euro_fmt(x, _pos=None):
    """Axis tick formatter — a plain module-level function (not a lambda) so
    the Figure that references it stays picklable for Streamlit's cache."""
    return f"€{x:,.0f}"


def _month_tick_interval(n_months: int) -> int:
    """
    How many months to skip between x-axis tick labels on a monthly time
    series. A bare MonthLocator() (one tick per month, every month) is cheap
    to reason about but not to draw: over a multi-year history it means
    matplotlib has to lay out dozens of two-line date labels — measured to
    cost more than a full second on this app's 6-panel spending chart alone,
    the single largest piece of that figure's ~2.7s render time (see
    plot_spending_analysis). Capping the tick count keeps every chart
    readable AND fast regardless of how much history has piled up.
    """
    return max(1, n_months // 18)


def _bar_nonzero(ax, x_dt, values, *, bottom=None, **kwargs):
    """
    ax.bar(), but without creating bars of height zero.

    A zero-height bar paints nothing — yet matplotlib still builds a Rectangle
    artist for it and then walks that rectangle's bezier path to update the
    axes' data limits. Profiled on this figure, that per-patch limit update
    (_update_patch_limits) was the single most expensive thing in the whole
    chart, and 659 of the top panel's 1,007 bars were zero: a category simply
    had no spending that month. Two thirds of the most expensive work in the
    figure was being done to draw blank space.

    Skipping them cannot change the picture — a zero-height rectangle covers no
    pixels — and that is verified by pixel-comparing the rendered PNG before
    and after.

    The one case that needs care is a series that is zero in EVERY month (say,
    no Gas at all inside a narrow date filter). It still has to appear in the
    legend, and in its own colour: an empty ax.bar([], []) leaves matplotlib
    with no patch to take the colour from, so that legend entry silently falls
    back to default blue. Drawing a single zero-height bar instead gives the
    legend a correctly coloured handle while still painting nothing — one
    artist rather than one per month.
    """
    values = np.asarray(values, dtype=float)
    keep = values != 0
    if not keep.any():
        if len(x_dt):
            base = None if bottom is None else np.asarray(bottom, dtype=float)[:1]
            ax.bar(list(x_dt)[:1], [0.0], bottom=base, **kwargs)
        else:
            ax.bar([], [], **kwargs)
        return
    xs = [x for x, k in zip(x_dt, keep) if k]
    heights = values[keep]
    bottoms = None if bottom is None else np.asarray(bottom, dtype=float)[keep]
    ax.bar(xs, heights, bottom=bottoms, **kwargs)


def _lock_month_xlim(ax, months_dt, width_days=18):
    """
    Pin the x-axis to the full month range.

    Because _bar_nonzero skips empty bars, a month in which nothing happened at
    all no longer contributes any artist to the axes — so left to its own
    autoscaling the axis could quietly end at a different month than before.
    This reproduces what matplotlib's autoscale would have chosen from the
    complete range (the bar extents plus its default 5% margin), so the drawn
    area is identical whether or not any bars were skipped.
    """
    if not months_dt:
        return
    lo = mdates.date2num(months_dt[0]) - width_days / 2
    hi = mdates.date2num(months_dt[-1]) + width_days / 2
    pad = (hi - lo) * 0.05
    ax.set_xlim(lo - pad, hi + pad)


# ── Figure 1: Full Spending Analysis ──────────────────────────────────────────

def plot_spending_analysis(
    d: dict,
    title: str = "COMBINED SPENDING ANALYSIS",
    subtitle: str = "",
) -> plt.Figure:
    """
    6-panel spending analysis figure:
      1. Monthly spending stacked bar (all categories)
      2. Grocery spend per month (by merchant)
      3. Dining & Food per month
      4. Utilities per month (stacked)
      5. Category totals bar chart
      6. Income vs spending trend

    Parameters
    ----------
    d        : Output of processor.process().
    title    : Main figure title.
    subtitle : Second line of the title (e.g. accounts + period).

    Returns
    -------
    matplotlib Figure
    """
    all_months_str  = d["all_months_str"]
    all_months_dt   = d["all_months_dt"]
    monthly_by_cat  = d["monthly_by_cat"]
    monthly_grocery = d["monthly_grocery"]
    grocery_detail  = d["grocery_detail"]
    monthly_dining  = d["monthly_dining"]
    monthly_electricity = d["monthly_electricity"]
    monthly_gas         = d["monthly_gas"]
    monthly_water       = d["monthly_water"]
    monthly_phone       = d["monthly_phone"]
    monthly_income      = d["monthly_income"]
    cat_totals      = d["cat_totals"]
    avg_last12      = d["avg_last12"]
    ci_last12       = d["ci_last12"]
    std_last12      = d["std_last12"]
    cutoff          = d["cutoff"]

    avg_grocery = sum(monthly_grocery.values()) / max(len([v for v in monthly_grocery.values() if v > 0]), 1)
    avg_dining  = sum(monthly_dining.values())  / max(len([v for v in monthly_dining.values()  if v > 0]), 1)

    cats_ordered = [
        "Rent", "Investments", "Education", "Entertainment",
        "Dining & Food", "Groceries", "Electricity", "Gas",
        "Transport", "Health & Beauty", "Gym", "Personal Care", "Water",
        "Phone", "Social & Gifts", "Fees & Tax", "Shopping", "Other",
    ]

    fig = plt.figure(figsize=(22, 30), facecolor=DARK_BG)
    suptitle = title + (f"\n{subtitle}" if subtitle else "")
    fig.suptitle(suptitle, fontsize=15, fontweight="bold", color=TEXT_COL, y=0.985)

    gs = gridspec.GridSpec(4, 2, figure=fig, hspace=0.48, wspace=0.32,
                           left=0.07, right=0.97, top=0.95, bottom=0.04)

    # ── Panel 1: Monthly spending stacked bar ─────────────────────────────────
    ax1 = fig.add_subplot(gs[0, :])
    ax1.set_facecolor(PANEL_BG)
    bottoms = np.zeros(len(all_months_str))
    for cat in cats_ordered:
        vals = np.array([monthly_by_cat[m].get(cat, 0) for m in all_months_str], dtype=float)
        _bar_nonzero(ax1, all_months_dt, vals, bottom=bottoms, width=18,
                     color=CAT_COLORS.get(cat, "#484f58"), label=cat, alpha=0.9)
        bottoms += vals

    first_cutoff_dt = datetime.strptime(cutoff[0], "%Y-%m")
    ax1.axhline(avg_last12, color=ACCENT3, linewidth=1.8, linestyle="--", zorder=10,
                label=f"Avg last 12mo excl. Jul+Aug  €{avg_last12:,.0f}  ±€{ci_last12:,.0f} (95% CI)  σ €{std_last12:,.0f}")
    ax1.axvspan(first_cutoff_dt, all_months_dt[-1], alpha=0.06, color=ACCENT3, zorder=0)

    ax1.set_title("Monthly Spending by Category — Combined Accounts", fontweight="bold", pad=10)
    ax1.set_ylabel("Amount (€)")
    ax1.yaxis.set_major_formatter(plt.FuncFormatter(_euro_fmt))
    ax1.xaxis.set_major_formatter(mdates.DateFormatter("%b\n%Y"))
    ax1.xaxis.set_major_locator(mdates.MonthLocator(interval=_month_tick_interval(len(all_months_dt))))
    ax1.legend(loc="upper left", fontsize=7.5, ncol=5)
    ax1.grid(True, axis="y")
    _lock_month_xlim(ax1, all_months_dt)

    # ── Panel 2: Grocery spend per month ─────────────────────────────────────
    ax2 = fig.add_subplot(gs[1, 0])
    ax2.set_facecolor(PANEL_BG)
    gr_months    = sorted(monthly_grocery.keys())
    gr_months_dt = [datetime.strptime(m, "%Y-%m") for m in gr_months]
    gr_vals      = [monthly_grocery[m] for m in gr_months]

    merch_order  = ["LIDL", "Mercadona", "Alcampo", "Simply", "Fruteria", "Obrador", "Other Grocery"]
    merch_colors = ["#56d364", "#3fb950", "#26a641", "#1a7f37", "#6bc8f5", "#79c0ff", "#8b949e"]
    bot          = np.zeros(len(gr_months))
    for merch, color in zip(merch_order, merch_colors):
        mvals = np.array([grocery_detail[m].get(merch, 0) for m in gr_months], dtype=float)
        _bar_nonzero(ax2, gr_months_dt, mvals, bottom=bot, width=18,
                     color=color, label=merch, alpha=0.9)
        bot += mvals

    ax2.axhline(avg_grocery, color=ACCENT3, linewidth=1.8, linestyle="--",
                label=f"Avg €{avg_grocery:.2f}")
    for dt_, v in zip(gr_months_dt, gr_vals):
        if v > 0:
            ax2.text(dt_, v + 1, f"{v:.0f}", ha="center", fontsize=7, color=TEXT_COL)

    ax2.set_title("🛒 Grocery Spend per Month", fontweight="bold", pad=10)
    ax2.set_ylabel("€")
    ax2.xaxis.set_major_formatter(mdates.DateFormatter("%b\n%Y"))
    ax2.xaxis.set_major_locator(mdates.MonthLocator(interval=_month_tick_interval(len(gr_months_dt))))
    ax2.legend(fontsize=8, ncol=2)
    ax2.grid(True, axis="y")
    _lock_month_xlim(ax2, gr_months_dt)

    # ── Panel 3: Dining & Food per month ─────────────────────────────────────
    ax3 = fig.add_subplot(gs[1, 1])
    ax3.set_facecolor(PANEL_BG)
    di_months    = sorted(monthly_dining.keys())
    di_months_dt = [datetime.strptime(m, "%Y-%m") for m in di_months]
    di_vals      = [monthly_dining[m] for m in di_months]

    _bar_nonzero(ax3, di_months_dt, di_vals, width=18, color=ACCENT5, alpha=0.85)
    ax3.axhline(avg_dining, color=ACCENT3, linewidth=1.8, linestyle="--",
                label=f"Avg €{avg_dining:.2f}")
    for dt_, v in zip(di_months_dt, di_vals):
        if v > 0:
            ax3.text(dt_, v + 1, f"{v:.0f}", ha="center", fontsize=7.5, color=TEXT_COL)

    ax3.set_title("🍽️ Dining & Food per Month", fontweight="bold", pad=10)
    ax3.set_ylabel("€")
    ax3.xaxis.set_major_formatter(mdates.DateFormatter("%b\n%Y"))
    ax3.xaxis.set_major_locator(mdates.MonthLocator(interval=_month_tick_interval(len(di_months_dt))))
    ax3.legend(fontsize=9)
    ax3.grid(True, axis="y")
    _lock_month_xlim(ax3, di_months_dt)

    # ── Panel 4: Utilities per month ─────────────────────────────────────────
    ax4 = fig.add_subplot(gs[2, 0])
    ax4.set_facecolor(PANEL_BG)
    util_months    = sorted(
        set(monthly_electricity) | set(monthly_gas) | set(monthly_water) | set(monthly_phone)
    )
    util_months_dt = [datetime.strptime(m, "%Y-%m") for m in util_months]
    util_order     = ["Electricity", "Gas", "Water", "Phone"]
    util_data      = [monthly_electricity, monthly_gas, monthly_water, monthly_phone]
    util_colors    = [CAT_COLORS[u] for u in util_order]
    util_bot       = np.zeros(len(util_months))

    for label, monthly, color in zip(util_order, util_data, util_colors):
        vals = np.array([monthly.get(m, 0) for m in util_months], dtype=float)
        _bar_nonzero(ax4, util_months_dt, vals, bottom=util_bot, width=18,
                     color=color, label=label, alpha=0.9)
        util_bot += vals

    ax4.set_title("⚡ Utilities per Month", fontweight="bold", pad=10)
    ax4.set_ylabel("€")
    ax4.xaxis.set_major_formatter(mdates.DateFormatter("%b\n%Y"))
    ax4.xaxis.set_major_locator(mdates.MonthLocator(interval=_month_tick_interval(len(util_months_dt))))
    ax4.legend(fontsize=9)
    ax4.grid(True, axis="y")
    _lock_month_xlim(ax4, util_months_dt)

    # ── Panel 5: Category totals bar chart ───────────────────────────────────
    ax5 = fig.add_subplot(gs[2, 1])
    ax5.set_facecolor(PANEL_BG)
    sorted_cats = sorted(
        [(cat, total) for cat, total in cat_totals.items() if cat != "Self-transfer"],
        key=lambda x: x[1],
    )
    labels  = [c for c, _ in sorted_cats]
    amounts = [a for _, a in sorted_cats]
    colors  = [CAT_COLORS.get(c, "#484f58") for c in labels]

    bars = ax5.barh(labels, amounts, color=colors, alpha=0.9)
    for bar_, amt in zip(bars, amounts):
        ax5.text(bar_.get_width() + 20, bar_.get_y() + bar_.get_height() / 2,
                 f"€{amt:,.0f}", va="center", fontsize=7.5, color=TEXT_COL)

    ax5.set_title("📊 Total Spending by Category", fontweight="bold", pad=10)
    ax5.xaxis.set_major_formatter(plt.FuncFormatter(_euro_fmt))
    ax5.grid(True, axis="x")

    # ── Panel 6: Monthly income vs spending trend ─────────────────────────────
    ax6 = fig.add_subplot(gs[3, :])
    ax6.set_facecolor(PANEL_BG)
    monthly_total_spending = {
        m: sum(monthly_by_cat[m].values()) for m in all_months_str
    }
    spend_vals  = [monthly_total_spending.get(m, 0) for m in all_months_str]
    income_vals = [d["monthly_income"].get(m, 0) for m in all_months_str]

    _bar_nonzero(ax6, all_months_dt, spend_vals,  width=18, color="#ff7b72", alpha=0.7, label="Spending")
    _bar_nonzero(ax6, all_months_dt, income_vals, width=18, color="#56d364", alpha=0.7, label="Income")
    ax6.set_title("💰 Monthly Income vs Total Spending", fontweight="bold", pad=10)
    ax6.set_ylabel("€")
    ax6.yaxis.set_major_formatter(plt.FuncFormatter(_euro_fmt))
    ax6.xaxis.set_major_formatter(mdates.DateFormatter("%b\n%Y"))
    ax6.xaxis.set_major_locator(mdates.MonthLocator(interval=_month_tick_interval(len(all_months_dt))))
    ax6.legend(fontsize=9)
    ax6.grid(True, axis="y")
    _lock_month_xlim(ax6, all_months_dt)

    # No tight_layout() here — unlike the other charts in this file, this one
    # sets its own explicit GridSpec margins above (left/right/top/bottom and
    # hspace/wspace), which tight_layout cannot override; matplotlib even warns
    # that this figure is 'not compatible with tight_layout'. So it changed
    # nothing about the layout while forcing an entire extra full-figure draw
    # to measure text — the figure was being rendered twice, once thrown away.
    # Verified by pixel-comparing the output with and without it: identical,
    # 0 pixels different, and ~400ms faster. The other five charts here DO
    # need theirs (removing them shifts ~500k pixels), so they keep it.
    return fig


# ── Figure: Net Worth Over Time ───────────────────────────────────────────────

def plot_net_worth_timeline(timeline: list[dict]) -> plt.Figure:
    """
    Net worth over time: cash (green) and invested (blue) stacked into an
    area chart, with total net worth traced as a bold line on top.

    Parameters
    ----------
    timeline : Output of processor.compute_net_worth_timeline() — a list of
               {"month", "cash", "invested", "net_worth"} dicts, oldest first.

    Returns
    -------
    matplotlib Figure
    """
    if not timeline:
        fig, ax = plt.subplots(facecolor=DARK_BG)
        ax.set_facecolor(PANEL_BG)
        ax.text(0.5, 0.5, "No data yet", ha="center", va="center",
                color=TEXT_COL, fontsize=14, transform=ax.transAxes)
        ax.set_axis_off()
        return fig

    months_dt = [datetime.strptime(t["month"], "%Y-%m") for t in timeline]
    cash      = [t["cash"] for t in timeline]
    invested  = [t["invested"] for t in timeline]
    net_worth = [t["net_worth"] for t in timeline]

    CASH_COL, INVEST_COL, NET_COL = "#3fb950", "#58a6ff", "#f0f6fc"

    fig, ax = plt.subplots(figsize=(18, 8), facecolor=DARK_BG)
    ax.set_facecolor(PANEL_BG)

    ax.stackplot(
        months_dt, cash, invested,
        labels=["Cash", "Invested"],
        colors=[CASH_COL, INVEST_COL],
        alpha=0.75,
        zorder=2,
    )
    ax.plot(months_dt, net_worth, color=NET_COL, linewidth=2.4,
            label="Net worth", zorder=5)
    ax.scatter([months_dt[-1]], [net_worth[-1]], color=NET_COL, s=45, zorder=6)
    ax.annotate(
        f"€{net_worth[-1]:,.0f}",
        (months_dt[-1], net_worth[-1]),
        textcoords="offset points", xytext=(8, 6),
        color=NET_COL, fontsize=10.5, fontweight="bold",
    )

    ax.axhline(0, color=GRID_COL, linewidth=1, zorder=1)
    ax.set_title("Net Worth Over Time", fontweight="bold", pad=12,
                 color=TEXT_COL, fontsize=14)
    ax.set_ylabel("€", color=TEXT_COL)
    ax.yaxis.set_major_formatter(plt.FuncFormatter(_euro_fmt))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b\n%Y"))
    # Space out month ticks so labels stay legible over long histories
    # instead of overlapping into an unreadable smear.
    tick_interval = max(1, len(months_dt) // 18)
    ax.xaxis.set_major_locator(mdates.MonthLocator(interval=tick_interval))
    ax.grid(True, axis="y", alpha=0.35)
    ax.legend(
        fontsize=9.5, loc="upper left", framealpha=0.2,
        labelcolor=TEXT_COL, facecolor=PANEL_BG, edgecolor=GRID_COL,
    )

    plt.tight_layout()
    return fig


def plot_allocation_timeline(timeline: list[dict]) -> plt.Figure:
    """
    Asset-class mix over time: a stacked area chart showing how the
    Equity / Fixed income / Commodities / Unspecified split of your invested
    money has shifted, month by month. The "Allocation" donut elsewhere only
    ever shows the mix as of right now — this shows how you got there.

    Parameters
    ----------
    timeline : Output of processor.compute_allocation_timeline() — a list of
               {"month", "by_class", "total"} dicts, oldest first.

    Returns
    -------
    matplotlib Figure
    """
    if not timeline or all(t["total"] <= 0 for t in timeline):
        fig, ax = plt.subplots(facecolor=DARK_BG)
        ax.set_facecolor(PANEL_BG)
        ax.text(0.5, 0.5, "No investment data found",
                ha="center", va="center", color=TEXT_COL, fontsize=14,
                transform=ax.transAxes)
        ax.set_axis_off()
        return fig

    # Fixed stacking order — color follows the asset class, never its
    # current rank, so a class doesn't change position (and implied color)
    # in the stack just because another one temporarily overtook it in size.
    CLASS_ORDER = ["Equity", "Fixed income", "Commodities", "Unspecified"]
    classes_present = [
        c for c in CLASS_ORDER
        if any(t["by_class"].get(c, 0) > 0 for t in timeline)
    ]

    months_dt = [datetime.strptime(t["month"], "%Y-%m") for t in timeline]
    series = [
        [t["by_class"].get(c, 0.0) for t in timeline]
        for c in classes_present
    ]

    fig, ax = plt.subplots(figsize=(18, 8), facecolor=DARK_BG)
    ax.set_facecolor(PANEL_BG)

    ax.stackplot(
        months_dt, *series,
        labels=classes_present,
        colors=[ASSET_CLASS_COLORS.get(c, "#8b949e") for c in classes_present],
        alpha=0.85,
    )

    ax.set_title("Asset Allocation Over Time", fontweight="bold", pad=12,
                 color=TEXT_COL, fontsize=14)
    ax.set_ylabel("€ invested", color=TEXT_COL)
    ax.yaxis.set_major_formatter(plt.FuncFormatter(_euro_fmt))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b\n%Y"))
    tick_interval = max(1, len(months_dt) // 18)
    ax.xaxis.set_major_locator(mdates.MonthLocator(interval=tick_interval))
    ax.grid(True, axis="y", alpha=0.35)
    ax.legend(
        fontsize=9.5, loc="upper left", framealpha=0.2,
        labelcolor=TEXT_COL, facecolor=PANEL_BG, edgecolor=GRID_COL,
    )

    plt.tight_layout()
    return fig


# ── Figure 2: Investment Allocation ───────────────────────────────────────────

def plot_investment_allocation(d: dict) -> plt.Figure:
    """
    Two donut charts: investment allocation by fund (left) and by account (right).

    Parameters
    ----------
    d : Output of processor.process().

    Returns
    -------
    matplotlib Figure
    """
    fund_names    = d["fund_names"]
    fund_amounts  = d["fund_amounts"]
    total_invested = d["total_invested"]
    account_totals = d["account_totals"]

    if total_invested == 0:
        fig, ax = plt.subplots(facecolor=DARK_BG)
        ax.set_facecolor(PANEL_BG)
        ax.text(0.5, 0.5, "No investment data found",
                ha="center", va="center", color=TEXT_COL, fontsize=14,
                transform=ax.transAxes)
        ax.set_axis_off()
        return fig

    DONUT = {"width": 0.45, "edgecolor": DARK_BG, "linewidth": 2}

    fig, (axA, axB) = plt.subplots(1, 2, figsize=(20, 10), facecolor=DARK_BG)
    fig.suptitle("Investment Allocation", fontsize=15,
                 fontweight="bold", color=TEXT_COL, y=1.01)

    # Left donut: by fund
    axA.set_facecolor(PANEL_BG)
    fund_colors_list = [FUND_COLORS.get(n, "#8b949e") for n in fund_names]
    wedgesA, _, autotextsA = axA.pie(
        fund_amounts,
        autopct=lambda p: f"{p:.1f}%" if p > 3 else "",
        colors=fund_colors_list,
        startangle=90, pctdistance=0.78,
        wedgeprops=DONUT,
        textprops={"color": TEXT_COL, "fontsize": 8},
    )
    for at in autotextsA:
        at.set_fontsize(8)
        at.set_color(DARK_BG)
        at.set_fontweight("bold")

    axA.text(0, 0, f"€{total_invested:,.0f}\ntotal",
             ha="center", va="center", fontsize=13, fontweight="bold", color=TEXT_COL)
    axA.set_title("By Fund", fontweight="bold", pad=16, color=TEXT_COL, fontsize=12)
    axA.legend(
        handles=[
            Patch(color=FUND_COLORS.get(n, "#8b949e"),
                  label=f"{n}  €{a:,.0f}  ({a/total_invested*100:.1f}%)")
            for n, a in zip(fund_names, fund_amounts)
        ],
        loc="lower center", bbox_to_anchor=(0.5, -0.22), ncol=1,
        fontsize=8.5, framealpha=0.2,
        labelcolor=TEXT_COL, facecolor=PANEL_BG, edgecolor=GRID_COL,
    )

    # Right donut: by account
    axB.set_facecolor(PANEL_BG)
    acc_labels = list(account_totals.keys())
    acc_vals   = [account_totals[a] for a in acc_labels]

    wedgesB, _, autotextsB = axB.pie(
        acc_vals,
        autopct="%1.1f%%",
        colors=[ACCOUNT_COLORS.get(a, "#8b949e") for a in acc_labels],
        startangle=90, pctdistance=0.78,
        wedgeprops=DONUT,
        textprops={"color": TEXT_COL, "fontsize": 10},
    )
    for at in autotextsB:
        at.set_fontsize(9.5)
        at.set_color(DARK_BG)
        at.set_fontweight("bold")

    axB.text(0, 0, f"€{total_invested:,.0f}\ntotal",
             ha="center", va="center", fontsize=13, fontweight="bold", color=TEXT_COL)
    axB.set_title("By Account", fontweight="bold", pad=16, color=TEXT_COL, fontsize=12)
    axB.legend(
        handles=[
            Patch(color=ACCOUNT_COLORS.get(a, "#8b949e"),
                  label=f"{a}  €{account_totals[a]:,.0f}  ({account_totals[a]/total_invested*100:.1f}%)")
            for a in acc_labels
        ],
        loc="lower center", bbox_to_anchor=(0.5, -0.1), ncol=1,
        fontsize=10, framealpha=0.2,
        labelcolor=TEXT_COL, facecolor=PANEL_BG, edgecolor=GRID_COL,
    )

    plt.tight_layout()
    return fig


def plot_investment_by_asset_class(d: dict) -> plt.Figure:
    """
    Single donut chart: investment allocation by asset class
    (Equity / Fixed income / Commodities / Unspecified).

    Parameters
    ----------
    d : Output of processor.process().

    Returns
    -------
    matplotlib Figure
    """
    asset_class_totals = d["asset_class_totals"]
    total_invested      = d["total_invested"]

    if total_invested == 0:
        fig, ax = plt.subplots(facecolor=DARK_BG)
        ax.set_facecolor(PANEL_BG)
        ax.text(0.5, 0.5, "No investment data found",
                ha="center", va="center", color=TEXT_COL, fontsize=14,
                transform=ax.transAxes)
        ax.set_axis_off()
        return fig

    DONUT = {"width": 0.45, "edgecolor": DARK_BG, "linewidth": 2}

    labels = sorted(asset_class_totals, key=lambda k: -asset_class_totals[k])
    values = [asset_class_totals[l] for l in labels]

    fig, ax = plt.subplots(figsize=(9, 9), facecolor=DARK_BG)
    ax.set_facecolor(PANEL_BG)

    wedges, _, autotexts = ax.pie(
        values,
        autopct="%1.1f%%",
        colors=[ASSET_CLASS_COLORS.get(l, "#8b949e") for l in labels],
        startangle=90, pctdistance=0.78,
        wedgeprops=DONUT,
        textprops={"color": TEXT_COL, "fontsize": 10},
    )
    for at in autotexts:
        at.set_fontsize(9.5)
        at.set_color(DARK_BG)
        at.set_fontweight("bold")

    ax.text(0, 0, f"€{total_invested:,.0f}\ntotal",
            ha="center", va="center", fontsize=13, fontweight="bold", color=TEXT_COL)
    ax.set_title("By Asset Class", fontweight="bold", pad=16, color=TEXT_COL, fontsize=12)
    ax.legend(
        handles=[
            Patch(color=ASSET_CLASS_COLORS.get(l, "#8b949e"),
                  label=f"{l}  €{asset_class_totals[l]:,.0f}  ({asset_class_totals[l]/total_invested*100:.1f}%)")
            for l in labels
        ],
        loc="lower center", bbox_to_anchor=(0.5, -0.18), ncol=1,
        fontsize=10, framealpha=0.2,
        labelcolor=TEXT_COL, facecolor=PANEL_BG, edgecolor=GRID_COL,
    )

    plt.tight_layout()
    return fig
