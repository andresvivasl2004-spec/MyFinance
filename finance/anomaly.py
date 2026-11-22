"""
anomaly.py — Detect and analyse anomalous spending months.

An "anomalous month" is one whose total spending falls outside the expected
range based on the inter-quartile range (IQR) method — the same technique
used by box plots.  These months skew averages and standard deviations, so
it is useful to flag them and optionally recompute stats without them.

The module also provides a "redistributed" view: the excess spend above the
normal range is spread evenly across all other months, giving a cleaner
picture of the underlying budget run-rate.

Usage (from notebook)
---------------------
    from finance.anomaly import detect, print_report, adjusted_process

    anomalies = detect(d)
    print_report(d, anomalies)

    # Recompute processor output excluding anomalous months
    d_clean = adjusted_process(all_data, anomalies, exclude_months=EXCLUDE_MONTHS)
"""

import numpy as np
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from collections import defaultdict
from datetime import datetime

from .style import DARK_BG, PANEL_BG, GRID_COL, TEXT_COL, ACCENT3, ACCENT5, CAT_COLORS
from . import processor as proc


def _euro_fmt(x, _pos=None):
    """Axis tick formatter — a plain module-level function (not a lambda) so
    the Figure that references it stays picklable for Streamlit's cache."""
    return f"€{x:,.0f}"


def _month_tick_interval(n_months: int) -> int:
    """How many months to skip between x-axis tick labels — see charts.py's
    copy of this function for why a bare MonthLocator() gets expensive."""
    return max(1, n_months // 18)


# ─────────────────────────────────────────────────────────────────────────────
# DETECTION
# ─────────────────────────────────────────────────────────────────────────────

def detect(
    d: dict,
    iqr_multiplier: float = 1.5,
    min_months: int = 6,
) -> dict:
    """
    Identify anomalous months using the IQR (box-plot) method.

    A month is anomalous if its total spending is above  Q3 + k*IQR  or
    below  Q1 - k*IQR  (k = iqr_multiplier, default 1.5).

    Parameters
    ----------
    d               : Output of processor.process().
    iqr_multiplier  : Sensitivity — 1.5 is standard, 3.0 is "extreme outlier only".
    min_months      : Skip detection if fewer than this many months are available.

    Returns
    -------
    dict with keys:
        "high"   : list of (month_str, total, excess) for high-spend anomalies
        "low"    : list of (month_str, total, deficit) for low-spend anomalies
        "q1"     : lower fence value
        "q3"     : upper fence value
        "iqr"    : IQR value
        "fence_high" : Q3 + k*IQR
        "fence_low"  : Q1 - k*IQR
        "normal_months" : list of months NOT flagged as anomalous
        "anomalous_months" : set of flagged month strings
    """
    all_months  = d["all_months_str"]
    totals      = d["monthly_totals_all"]

    if len(all_months) < min_months:
        print(f"  ⚠ Only {len(all_months)} months — need at least {min_months} for anomaly detection.")
        return _empty_result(all_months)

    vals = np.array([totals.get(m, 0) for m in all_months])

    q1, q3  = float(np.percentile(vals, 25)), float(np.percentile(vals, 75))
    iqr     = q3 - q1
    fence_h = q3 + iqr_multiplier * iqr
    fence_l = max(0.0, q1 - iqr_multiplier * iqr)   # can't have negative spending floor

    high, low = [], []
    anomalous = set()

    for m, v in zip(all_months, vals):
        if v > fence_h:
            high.append((m, float(v), float(v - fence_h)))
            anomalous.add(m)
        elif v < fence_l:
            low.append((m, float(v), float(fence_l - v)))
            anomalous.add(m)

    normal_months = [m for m in all_months if m not in anomalous]

    return {
        "high":             sorted(high,  key=lambda x: -x[2]),
        "low":              sorted(low,   key=lambda x: -x[2]),
        "q1":               q1,
        "q3":               q3,
        "iqr":              iqr,
        "fence_high":       fence_h,
        "fence_low":        fence_l,
        "normal_months":    normal_months,
        "anomalous_months": anomalous,
        "iqr_multiplier":   iqr_multiplier,
    }


def _empty_result(all_months):
    return {
        "high": [], "low": [], "q1": 0, "q3": 0, "iqr": 0,
        "fence_high": 0, "fence_low": 0,
        "normal_months": all_months, "anomalous_months": set(),
        "iqr_multiplier": 1.5,
    }


# ─────────────────────────────────────────────────────────────────────────────
# REPORT
# ─────────────────────────────────────────────────────────────────────────────

def print_report(d: dict, anomalies: dict) -> None:
    """
    Print a detailed anomaly report showing:
    - Which months are flagged and by how much
    - Which categories drove the anomaly
    - Redistributed monthly average (excess spread across normal months)
    """
    all_months  = d["all_months_str"]
    totals      = d["monthly_totals_all"]
    monthly_cat = d["monthly_by_cat"]

    high = anomalies["high"]
    low  = anomalies["low"]
    k    = anomalies["iqr_multiplier"]

    print(f"\n{'='*66}")
    print(f"  ANOMALOUS MONTH ANALYSIS  (IQR ×{k})")
    print(f"{'='*66}")
    print(f"  Total months analysed : {len(all_months)}")
    print(f"  Normal spending range : €{anomalies['fence_low']:,.0f}  –  €{anomalies['fence_high']:,.0f}")
    print(f"  Q1 / Median / Q3      : €{anomalies['q1']:,.0f}  /  "
          f"€{np.median([totals.get(m,0) for m in all_months]):,.2f}  /  "
          f"€{anomalies['q3']:,.0f}")
    print(f"  IQR                   : €{anomalies['iqr']:,.0f}")

    total_anomalous = len(high) + len(low)
    if total_anomalous == 0:
        print(f"\n  ✅ No anomalous months detected — spending is consistent.")
        return

    print(f"  Anomalous months      : {total_anomalous}  "
          f"({len(high)} high, {len(low)} low)\n")

    # High-spend anomalies
    if high:
        print(f"  ── HIGH-SPEND MONTHS  (above €{anomalies['fence_high']:,.0f}) {'─'*30}")
        print(f"  {'Month':<10}  {'Total':>10}  {'Excess':>10}  Top categories")
        print(f"  {'─'*10}  {'─'*10}  {'─'*10}  {'─'*35}")
        for month, total, excess in high:
            top_cats = _top_categories(monthly_cat[month], n=3)
            print(f"  {month:<10}  €{total:>9,.0f}  €{excess:>9,.0f}  {top_cats}")
            _print_category_detail(monthly_cat[month])

    # Low-spend anomalies
    if low:
        print(f"\n  ── LOW-SPEND MONTHS  (below €{anomalies['fence_low']:,.0f}) {'─'*31}")
        print(f"  {'Month':<10}  {'Total':>10}  {'Deficit':>10}  Note")
        print(f"  {'─'*10}  {'─'*10}  {'─'*10}  {'─'*35}")
        for month, total, deficit in low:
            print(f"  {month:<10}  €{total:>9,.0f}  €{deficit:>9,.0f}  "
                  f"(possibly incomplete data)")

    # Redistribution
    _print_redistribution(d, anomalies)


def _top_categories(month_cats: dict, n: int = 3) -> str:
    sorted_cats = sorted(month_cats.items(), key=lambda x: -x[1])[:n]
    return "  |  ".join(f"{cat} €{val:,.0f}" for cat, val in sorted_cats)


def _print_category_detail(month_cats: dict) -> None:
    sorted_cats = sorted(month_cats.items(), key=lambda x: -x[1])
    for cat, val in sorted_cats:
        if val > 0:
            bar = "▓" * min(int(val / 30), 25)
            print(f"      {cat:<22}  €{val:>8,.2f}  {bar}")


def _print_redistribution(d: dict, anomalies: dict) -> None:
    """Show what the average would look like with excess redistributed."""
    all_months      = d["all_months_str"]
    totals          = d["monthly_totals_all"]
    normal_months   = anomalies["normal_months"]
    fence_h         = anomalies["fence_high"]

    if not anomalies["high"]:
        return

    total_excess = sum(excess for _, _, excess in anomalies["high"])
    n_normal     = len(normal_months)

    if n_normal == 0:
        return

    redistrib_per_month = total_excess / n_normal
    normal_vals         = [totals.get(m, 0) for m in normal_months]
    avg_normal          = float(np.mean(normal_vals))
    avg_with_redistrib  = avg_normal + redistrib_per_month

    avg_raw = float(np.mean([totals.get(m, 0) for m in all_months]))

    print(f"\n  ── REDISTRIBUTION SUMMARY {'─'*40}")
    print(f"  Total excess spend (high months)    : €{total_excess:>9,.2f}")
    print(f"  Redistributed across {n_normal} normal months : +€{redistrib_per_month:>8,.2f} / month")
    print(f"\n  {'Avg (all months, raw)':<38}: €{avg_raw:>9,.2f}")
    print(f"  {'Avg (normal months only)':<38}: €{avg_normal:>9,.2f}")
    print(f"  {'Avg (normal + redistributed excess)':<38}: €{avg_with_redistrib:>9,.2f}")
    print(f"\n  → Use €{avg_with_redistrib:,.0f}/month as your realistic budget baseline.")
    print(f"{'='*66}\n")


# ─────────────────────────────────────────────────────────────────────────────
# ADJUSTED ANALYSIS
# ─────────────────────────────────────────────────────────────────────────────

def adjusted_process(
    all_data: list[tuple],
    anomalies: dict,
    exclude_months: set | None = None,
    summer_months: set | None = None,
    start_month: str | None = None,
    end_month: str | None = None,
) -> dict:
    """
    Re-run processor.process() excluding anomalous months.
    Use this to get clean averages and stats unaffected by outlier months.

    Parameters
    ----------
    all_data        : Full transaction list from loader/storage.
    anomalies       : Output of detect().
    exclude_months  : Additional months to exclude (same as in normal analysis).
    summer_months   : Months to exclude from last-12mo average (default {07, 08}).
    start_month     : Optional filter start "YYYY-MM".
    end_month       : Optional filter end "YYYY-MM".

    Returns
    -------
    processor.process() dict, same structure as the normal analysis.
    """
    combined_exclude = set(anomalies["anomalous_months"])
    if exclude_months:
        combined_exclude |= set(exclude_months)

    d = proc.process(
        all_data,
        exclude_months=combined_exclude,
        summer_months=summer_months,
        start_month=start_month,
        end_month=end_month,
    )
    print(f"  ℹ️  Analysis excludes {len(anomalies['anomalous_months'])} anomalous month(s): "
          f"{', '.join(sorted(anomalies['anomalous_months']))}")
    return d


# ─────────────────────────────────────────────────────────────────────────────
# CHART
# ─────────────────────────────────────────────────────────────────────────────

def plot_anomaly_overview(d: dict, anomalies: dict) -> plt.Figure:
    """
    Bar chart of monthly spending with IQR fences and anomalous months highlighted.
    """
    all_months   = d["all_months_str"]
    all_months_dt = d["all_months_dt"]
    totals       = d["monthly_totals_all"]
    anomalous    = anomalies["anomalous_months"]
    fence_h      = anomalies["fence_high"]
    fence_l      = anomalies["fence_low"]
    q1, q3       = anomalies["q1"], anomalies["q3"]

    vals   = [totals.get(m, 0) for m in all_months]
    colors = [
        "#ff7b72" if m in anomalous else "#58a6ff"
        for m in all_months
    ]

    fig, ax = plt.subplots(figsize=(18, 7), facecolor=DARK_BG)
    ax.set_facecolor(PANEL_BG)

    ax.bar(all_months_dt, vals, width=18, color=colors, alpha=0.88)

    # IQR fences
    ax.axhline(fence_h, color="#ffa657", linewidth=1.6, linestyle="--",
               label=f"Upper fence  €{fence_h:,.0f}  (Q3 + {anomalies['iqr_multiplier']}×IQR)")
    if fence_l > 0:
        ax.axhline(fence_l, color="#ffa657", linewidth=1.6, linestyle=":",
                   label=f"Lower fence  €{fence_l:,.0f}")

    # Q1–Q3 band
    ax.axhspan(q1, q3, alpha=0.08, color="#3fb950", label=f"IQR band  €{q1:,.0f} – €{q3:,.0f}")

    # Labels on anomalous bars
    for m, dt_, v in zip(all_months, all_months_dt, vals):
        if m in anomalous:
            ax.text(dt_, v + max(vals) * 0.01, f"€{v:,.0f}",
                    ha="center", fontsize=7.5, color="#ff7b72", fontweight="bold")

    ax.set_title("Monthly Spending — Anomalous Months Highlighted",
                 fontweight="bold", pad=12, color=TEXT_COL, fontsize=13)
    ax.set_ylabel("Total Spending (€)", color=TEXT_COL)
    ax.yaxis.set_major_formatter(plt.FuncFormatter(_euro_fmt))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b\n%Y"))
    ax.xaxis.set_major_locator(mdates.MonthLocator(interval=_month_tick_interval(len(all_months_dt))))
    ax.legend(fontsize=8.5, loc="upper left")
    ax.grid(True, axis="y", alpha=0.4)

    from matplotlib.patches import Patch
    legend_extra = [
        Patch(color="#ff7b72", alpha=0.88, label="Anomalous month"),
        Patch(color="#58a6ff", alpha=0.88, label="Normal month"),
    ]
    ax.legend(handles=ax.get_legend_handles_labels()[0] + legend_extra,
              labels=ax.get_legend_handles_labels()[1] + ["Anomalous month", "Normal month"],
              fontsize=8.5, loc="upper left", ncol=2)

    plt.tight_layout()
    return fig
