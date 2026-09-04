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
    CAT_COLORS, ACCOUNT_COLORS, FUND_COLORS,
)


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
        vals = [monthly_by_cat[m].get(cat, 0) for m in all_months_str]
        ax1.bar(all_months_dt, vals, bottom=bottoms, width=18,
                color=CAT_COLORS.get(cat, "#484f58"), label=cat, alpha=0.9)
        bottoms += np.array(vals)

    first_cutoff_dt = datetime.strptime(cutoff[0], "%Y-%m")
    ax1.axhline(avg_last12, color=ACCENT3, linewidth=1.8, linestyle="--", zorder=10,
                label=f"Avg last 12mo excl. Jul+Aug  €{avg_last12:,.0f}  ±€{ci_last12:,.0f} (95% CI)  σ €{std_last12:,.0f}")
    ax1.axvspan(first_cutoff_dt, all_months_dt[-1], alpha=0.06, color=ACCENT3, zorder=0)

    ax1.set_title("Monthly Spending by Category — Combined Accounts", fontweight="bold", pad=10)
    ax1.set_ylabel("Amount (€)")
    ax1.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"€{x:,.0f}"))
    ax1.xaxis.set_major_formatter(mdates.DateFormatter("%b\n%Y"))
    ax1.xaxis.set_major_locator(mdates.MonthLocator())
    ax1.legend(loc="upper left", fontsize=7.5, ncol=5)
    ax1.grid(True, axis="y")

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
        mvals = [grocery_detail[m].get(merch, 0) for m in gr_months]
        ax2.bar(gr_months_dt, mvals, bottom=bot, width=18, color=color, label=merch, alpha=0.9)
        bot += np.array(mvals)

    ax2.axhline(avg_grocery, color=ACCENT3, linewidth=1.8, linestyle="--",
                label=f"Avg €{avg_grocery:.2f}")
    for dt_, v in zip(gr_months_dt, gr_vals):
        if v > 0:
            ax2.text(dt_, v + 1, f"{v:.0f}", ha="center", fontsize=7, color=TEXT_COL)

    ax2.set_title("🛒 Grocery Spend per Month", fontweight="bold", pad=10)
    ax2.set_ylabel("€")
    ax2.xaxis.set_major_formatter(mdates.DateFormatter("%b\n%Y"))
    ax2.xaxis.set_major_locator(mdates.MonthLocator())
    ax2.legend(fontsize=8, ncol=2)
    ax2.grid(True, axis="y")

    # ── Panel 3: Dining & Food per month ─────────────────────────────────────
    ax3 = fig.add_subplot(gs[1, 1])
    ax3.set_facecolor(PANEL_BG)
    di_months    = sorted(monthly_dining.keys())
    di_months_dt = [datetime.strptime(m, "%Y-%m") for m in di_months]
    di_vals      = [monthly_dining[m] for m in di_months]

    ax3.bar(di_months_dt, di_vals, width=18, color=ACCENT5, alpha=0.85)
    ax3.axhline(avg_dining, color=ACCENT3, linewidth=1.8, linestyle="--",
                label=f"Avg €{avg_dining:.2f}")
    for dt_, v in zip(di_months_dt, di_vals):
        if v > 0:
            ax3.text(dt_, v + 1, f"{v:.0f}", ha="center", fontsize=7.5, color=TEXT_COL)

    ax3.set_title("🍽️ Dining & Food per Month", fontweight="bold", pad=10)
    ax3.set_ylabel("€")
    ax3.xaxis.set_major_formatter(mdates.DateFormatter("%b\n%Y"))
    ax3.xaxis.set_major_locator(mdates.MonthLocator())
    ax3.legend(fontsize=9)
    ax3.grid(True, axis="y")

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
        vals = [monthly.get(m, 0) for m in util_months]
        ax4.bar(util_months_dt, vals, bottom=util_bot, width=18,
                color=color, label=label, alpha=0.9)
        util_bot += np.array(vals)

    ax4.set_title("⚡ Utilities per Month", fontweight="bold", pad=10)
    ax4.set_ylabel("€")
    ax4.xaxis.set_major_formatter(mdates.DateFormatter("%b\n%Y"))
    ax4.xaxis.set_major_locator(mdates.MonthLocator())
    ax4.legend(fontsize=9)
    ax4.grid(True, axis="y")

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
    ax5.xaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"€{x:,.0f}"))
    ax5.grid(True, axis="x")

    # ── Panel 6: Monthly income vs spending trend ─────────────────────────────
    ax6 = fig.add_subplot(gs[3, :])
    ax6.set_facecolor(PANEL_BG)
    monthly_total_spending = {
        m: sum(monthly_by_cat[m].values()) for m in all_months_str
    }
    spend_vals  = [monthly_total_spending.get(m, 0) for m in all_months_str]
    income_vals = [d["monthly_income"].get(m, 0) for m in all_months_str]

    ax6.bar(all_months_dt, spend_vals,  width=18, color="#ff7b72", alpha=0.7, label="Spending")
    ax6.bar(all_months_dt, income_vals, width=18, color="#56d364", alpha=0.7, label="Income")
    ax6.set_title("💰 Monthly Income vs Total Spending", fontweight="bold", pad=10)
    ax6.set_ylabel("€")
    ax6.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"€{x:,.0f}"))
    ax6.xaxis.set_major_formatter(mdates.DateFormatter("%b\n%Y"))
    ax6.xaxis.set_major_locator(mdates.MonthLocator())
    ax6.legend(fontsize=9)
    ax6.grid(True, axis="y")

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
