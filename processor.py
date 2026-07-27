"""
processor.py — Aggregate transactions into monthly totals, stats, and summaries.

Call `process(all_data)` to get back a single dict with every computed value
that the charts and summary printer need.
"""

import numpy as np
from collections import defaultdict, Counter
from datetime import datetime


# ── Grocery merchant detection ────────────────────────────────────────────────
GROCERY_MERCHANTS = {
    "LIDL":      ["LIDL"],
    "Mercadona": ["MERCADONA"],
    "Alcampo":   ["ALCAMPO"],
    "Simply":    ["SIMPLY", "BRAVO MURILLO"],
    "Fruteria":  ["FRUTERIA", "FRUTAS"],
    "Obrador":   ["OBRADOR"],
}

# ── Investment fund detection ─────────────────────────────────────────────────
INVESTMENT_FUNDS = {
    "iShares Physical Gold ETC":        (["ISHARES PHYSICAL GOLD"],           "Trade Republic"),
    "iShares Core MSCI World (Acc)":    (["ISHARES CORE MSCI WORLD"],         "Trade Republic"),
    "iShares Developed World":          (["ISHARES DEVELOPED WORLD"],         "MyInvestor"),
    "AMUNDI INDEX MSCI World AE Dis":   (["AMUNDI INDEX MSCI WORLD AE DIS"],  "MyInvestor"),
    "AMUNDI INDEX S&P 500 ESG AE Acc":  (["AMUNDI INDEX S&P 500 ESG",
                                          "INDEX S&P 500 ESG AE ACC"],        "MyInvestor"),
    "INDEX MSCI World AE Dis EUR":      (["INDEX MSCI WORLD AE DIS EUR"],     "MyInvestor"),
    "ROBECO BP Global Premium EQ D":    (["ROBECO BP GLOBAL PREMIUM",
                                          "GLOBAL PREMIUM EQ D"],             "MyInvestor"),
    "BBVA Investment Funds":            (["BBVA"],                             "BBVA"),
}


# ── Helper stats functions ────────────────────────────────────────────────────

def _avg(d: dict) -> float:
    vals = [v for v in d.values() if v > 0]
    return float(np.mean(vals)) if vals else 0.0


def _std(d: dict) -> float:
    vals = [v for v in d.values() if v > 0]
    return float(np.std(vals, ddof=1)) if len(vals) > 1 else 0.0


def _ci(d: dict, z: float = 1.96) -> float:
    """95 % confidence interval half-width (±) around the mean."""
    vals = [v for v in d.values() if v > 0]
    n = len(vals)
    return z * float(np.std(vals, ddof=1)) / np.sqrt(n) if n > 1 else 0.0


def _n(d: dict) -> int:
    return len([v for v in d.values() if v > 0])


def _detect_grocery_merchant(description: str) -> str:
    desc_upper = description.upper()
    for name, keywords in GROCERY_MERCHANTS.items():
        if any(kw in desc_upper for kw in keywords):
            return name
    return "Other Grocery"


def _detect_investment_fund(description: str) -> tuple[str, str]:
    """Returns (fund_name, account_name)."""
    desc_upper = description.upper()
    for fund_name, (keywords, account) in INVESTMENT_FUNDS.items():
        if any(kw in desc_upper for kw in keywords):
            return fund_name, account
    return "Other Investment", "Unknown"


# ── Main processing function ──────────────────────────────────────────────────

def process(
    all_data: list[tuple],
    exclude_months: set[str] | None = None,
    summer_months: set[str] | None = None,
    start_month: str | None = None,
    end_month: str | None = None,
) -> dict:
    """
    Aggregate all transactions into monthly totals, averages, and statistics.

    Parameters
    ----------
    all_data       : Output of loader/storage — list of
                     (datetime, float, str, str, str) tuples.
    exclude_months : Set of "YYYY-MM" strings to drop from the analysis.
    summer_months  : Month numbers to exclude from the last-12mo average.
                     Default {"07", "08"}.
    start_month    : Optional "YYYY-MM" — analyse only from this month onwards.
    end_month      : Optional "YYYY-MM" — analyse only up to this month.
                     Example: start_month="2025-01", end_month="2025-12"

    Returns
    -------
    A dict with all computed values needed by charts.py and the summary printer.
    """
    if exclude_months is None:
        exclude_months = set()
    if summer_months is None:
        summer_months = {"07", "08"}

    # ── Apply date range filter ───────────────────────────────────────────────
    if start_month or end_month:
        def _in_range(dt):
            mk = dt.strftime("%Y-%m")
            if start_month and mk < start_month:
                return False
            if end_month and mk > end_month:
                return False
            return True
        all_data = [r for r in all_data if _in_range(r[0])]
        if start_month or end_month:
            label = f"{start_month or '?'} → {end_month or '?'}"
            print(f"  📅 Date filter applied: {label}  ({len(all_data)} transactions)")

    # ── Monthly bucketing ─────────────────────────────────────────────────────
    monthly_by_cat      = defaultdict(lambda: defaultdict(float))
    monthly_grocery     = defaultdict(float)
    monthly_dining      = defaultdict(float)
    monthly_electricity = defaultdict(float)
    monthly_gas         = defaultdict(float)
    monthly_water       = defaultdict(float)
    monthly_phone       = defaultdict(float)
    monthly_investments = defaultdict(float)
    monthly_income      = defaultdict(float)
    monthly_interest    = defaultdict(float)
    grocery_detail      = defaultdict(lambda: defaultdict(float))
    investments_detail  = defaultdict(lambda: defaultdict(float))

    for dt, amount, desc, cat, source in all_data:
        if cat == "Self-transfer":
            continue

        mk = dt.strftime("%Y-%m")

        if cat == "Income":
            monthly_income[mk] += amount
            continue
        if cat == "Interest":
            monthly_interest[mk] += amount
            continue
        if amount >= 0:
            continue   # skip positive amounts that aren't income/interest

        abs_amt = abs(amount)
        monthly_by_cat[mk][cat] += abs_amt

        if cat == "Groceries":
            monthly_grocery[mk] += abs_amt
            merchant = _detect_grocery_merchant(desc)
            grocery_detail[mk][merchant] += abs_amt
        elif cat == "Investments":
            monthly_investments[mk] += abs_amt
            fund_name, _ = _detect_investment_fund(desc)
            investments_detail[mk][fund_name] += abs_amt
        elif cat == "Dining & Food":
            monthly_dining[mk] += abs_amt
        elif cat == "Electricity":
            monthly_electricity[mk] += abs_amt
        elif cat == "Gas":
            monthly_gas[mk] += abs_amt
        elif cat == "Water":
            monthly_water[mk] += abs_amt
        elif cat == "Phone":
            monthly_phone[mk] += abs_amt

    # ── Month lists ───────────────────────────────────────────────────────────
    all_months_str = sorted(
        m for m in monthly_by_cat if m not in exclude_months
    )
    all_months_dt = [datetime.strptime(m, "%Y-%m") for m in all_months_str]

    # ── Category totals ───────────────────────────────────────────────────────
    cat_totals: dict[str, float] = defaultdict(float)
    for mk in all_months_str:
        for cat_name, val in monthly_by_cat[mk].items():
            if cat_name != "Self-transfer":
                cat_totals[cat_name] += val

    total_income      = sum(monthly_income.values())
    total_interest    = sum(monthly_interest.values())
    total_investments = sum(monthly_investments.values())
    total_spending    = sum(cat_totals.values()) - total_investments
    total_inflows     = total_income + total_investments + total_interest
    net_worth         = total_inflows - total_spending

    # ── Per-category stats (active months only) ───────────────────────────────
    cat_monthly_avg: dict[str, float] = {}
    cat_monthly_std: dict[str, float] = {}
    cat_monthly_ci:  dict[str, float] = {}
    for cat in cat_totals:
        active = [
            monthly_by_cat[m][cat]
            for m in all_months_str
            if monthly_by_cat[m].get(cat, 0) > 0
        ]
        n_active = len(active)
        cat_monthly_avg[cat] = float(np.mean(active)) if active else 0.0
        cat_monthly_std[cat] = float(np.std(active, ddof=1)) if n_active > 1 else 0.0
        cat_monthly_ci[cat]  = (
            1.96 * cat_monthly_std[cat] / np.sqrt(n_active) if n_active > 1 else 0.0
        )

    # ── Total monthly spending stats ──────────────────────────────────────────
    monthly_totals_all = {
        m: sum(monthly_by_cat[m].values())
        for m in all_months_str
    }
    _total_vals       = list(monthly_totals_all.values())
    total_monthly_avg = float(np.mean(_total_vals)) if _total_vals else 0.0
    total_monthly_std = float(np.std(_total_vals, ddof=1)) if len(_total_vals) > 1 else 0.0
    total_monthly_ci  = (
        1.96 * total_monthly_std / np.sqrt(len(_total_vals)) if len(_total_vals) > 1 else 0.0
    )

    # ── Income stats ──────────────────────────────────────────────────────────
    n_income_months = len(all_months_str)
    avg_income = total_income / n_income_months if n_income_months else 0.0
    std_income = float(np.std(
        [monthly_income.get(m, 0) for m in all_months_str], ddof=1
    ))
    ci_income = 1.96 * std_income / np.sqrt(n_income_months) if n_income_months > 1 else 0.0

    # ── Last-12-months averages (excluding summer) ────────────────────────────
    cutoff        = sorted(all_months_str)[-12:]
    active_months = [m for m in cutoff if m.split("-")[1] not in summer_months]

    def _filter(d):
        return {m: d[m] for m in active_months if m in d}

    monthly_totals_active = {
        m: sum(monthly_by_cat[m].values())
        for m in active_months
    }
    _vals_last12          = list(monthly_totals_active.values())
    avg_last12            = float(np.mean(_vals_last12)) if _vals_last12 else 0.0
    std_last12            = float(np.std(_vals_last12, ddof=1)) if len(_vals_last12) > 1 else 0.0
    ci_last12             = (
        1.96 * std_last12 / np.sqrt(len(_vals_last12)) if len(_vals_last12) > 1 else 0.0
    )

    # ── Investment allocation ─────────────────────────────────────────────────
    fund_totals: dict[str, float] = defaultdict(float)
    for mk, funds in investments_detail.items():
        for fund, amt in funds.items():
            fund_totals[fund] += amt

    fund_names   = [f for f, _ in sorted(fund_totals.items(), key=lambda x: -x[1])]
    fund_amounts = [fund_totals[f] for f in fund_names]

    fund_account_map = {
        fund_name: account
        for fund_name, (_, account) in INVESTMENT_FUNDS.items()
    }
    fund_account_map["Other Investment"] = "Unknown"

    fund_accounts  = [fund_account_map.get(f, "Unknown") for f in fund_names]
    total_invested = sum(fund_amounts)

    account_totals: Counter = Counter()
    for name, amt, acc in zip(fund_names, fund_amounts, fund_accounts):
        account_totals[acc] += amt

    # ── Merchant totals (grocery) ─────────────────────────────────────────────
    merchant_totals: dict[str, float] = defaultdict(float)
    for mk, merchants in grocery_detail.items():
        for merch, val in merchants.items():
            merchant_totals[merch] += val

    return {
        # Raw monthly dicts
        "monthly_by_cat":       monthly_by_cat,
        "monthly_grocery":      monthly_grocery,
        "monthly_dining":       monthly_dining,
        "monthly_electricity":  monthly_electricity,
        "monthly_gas":          monthly_gas,
        "monthly_water":        monthly_water,
        "monthly_phone":        monthly_phone,
        "monthly_investments":  monthly_investments,
        "monthly_income":       monthly_income,
        "monthly_interest":     monthly_interest,
        "grocery_detail":       grocery_detail,
        "investments_detail":   investments_detail,
        # Month lists
        "all_months_str":       all_months_str,
        "all_months_dt":        all_months_dt,
        "active_months":        active_months,
        "cutoff":               cutoff,
        # Category totals
        "cat_totals":           dict(cat_totals),
        "cat_monthly_avg":      cat_monthly_avg,
        "cat_monthly_std":      cat_monthly_std,
        "cat_monthly_ci":       cat_monthly_ci,
        # Grand totals
        "total_income":         total_income,
        "total_interest":       total_interest,
        "total_investments":    total_investments,
        "total_spending":       total_spending,
        "total_inflows":        total_inflows,
        "net_worth":            net_worth,
        # Income stats
        "n_income_months":      n_income_months,
        "avg_income":           avg_income,
        "std_income":           std_income,
        "ci_income":            ci_income,
        # Overall monthly spending stats
        "monthly_totals_all":   monthly_totals_all,
        "total_monthly_avg":    total_monthly_avg,
        "total_monthly_std":    total_monthly_std,
        "total_monthly_ci":     total_monthly_ci,
        # Last-12-month stats (excl. summer)
        "monthly_totals_active": monthly_totals_active,
        "avg_last12":            avg_last12,
        "std_last12":            std_last12,
        "ci_last12":             ci_last12,
        # Utility last-12 stats
        "avg_last12_electricity": _avg(_filter(monthly_electricity)),
        "std_last12_electricity": _std(_filter(monthly_electricity)),
        "ci_last12_electricity":  _ci(_filter(monthly_electricity)),
        "avg_last12_gas":         _avg(_filter(monthly_gas)),
        "std_last12_gas":         _std(_filter(monthly_gas)),
        "ci_last12_gas":          _ci(_filter(monthly_gas)),
        "avg_last12_water":       _avg(_filter(monthly_water)),
        "std_last12_water":       _std(_filter(monthly_water)),
        "ci_last12_water":        _ci(_filter(monthly_water)),
        "avg_last12_phone":       _avg(_filter(monthly_phone)),
        "std_last12_phone":       _std(_filter(monthly_phone)),
        "ci_last12_phone":        _ci(_filter(monthly_phone)),
        # Investment allocation
        "fund_names":       fund_names,
        "fund_amounts":     fund_amounts,
        "fund_accounts":    fund_accounts,
        "total_invested":   total_invested,
        "account_totals":   dict(account_totals),
        "merchant_totals":  dict(merchant_totals),
    }


def print_summary(d: dict, accounts_label: str = "", period_label: str = "") -> None:
    """
    Print the full text summary table to stdout.

    Parameters
    ----------
    d              : Output of process().
    accounts_label : e.g. "MyInvestor · Trade Republic · BBVA"
    period_label   : e.g. "Mar 2022 – May 2026"
    """
    cat_totals     = d["cat_totals"]
    cat_monthly_avg = d["cat_monthly_avg"]
    cat_monthly_std = d["cat_monthly_std"]
    cat_monthly_ci  = d["cat_monthly_ci"]
    all_months_str  = d["all_months_str"]
    monthly_by_cat  = d["monthly_by_cat"]
    active_months   = d["active_months"]
    monthly_totals_active = d["monthly_totals_active"]

    print("=" * 70)
    print("  COMBINED SPENDING ANALYSIS")
    if accounts_label:
        print(f"  Accounts: {accounts_label}")
    if period_label:
        print(f"  Period: {period_label}")
    print("=" * 70)

    print(f"\n  {'Category':<22} {'Total':>10}   {'Avg/Month':>10}   "
          f"{'Std Dev':>9}   {'95% CI (±)':>11}   {'Months':>6}")
    print(f"  {'─'*22}   {'─'*10}   {'─'*10}   {'─'*9}   {'─'*11}   {'─'*6}")

    for cat, total in sorted(cat_totals.items(), key=lambda x: -x[1]):
        n   = _n({m: monthly_by_cat[m].get(cat, 0) for m in all_months_str})
        avg = cat_monthly_avg.get(cat, 0)
        std = cat_monthly_std.get(cat, 0)
        ci  = cat_monthly_ci.get(cat, 0)
        print(f"  {cat:<22} €{total:>9,.2f}   €{avg:>9,.2f}   "
              f"€{std:>8,.2f}   ±€{ci:>9,.2f}   {n:>6}")

    print("─" * 100)
    print(f"  {'Income':<22} €{d['total_income']:>9,.2f}   €{d['avg_income']:>9,.2f}   "
          f"€{d['std_income']:>8,.2f}   ±€{d['ci_income']:>9,.2f}   {d['n_income_months']:>6}")

    print(f"\n  {'─'*20}   {'─'*12}")
    print(f"  {'Total Inflows':<20} €{d['total_inflows']:>12,.2f}")
    print(f"  {'Spending':<20} €{-d['total_spending']:>12,.2f}")
    print(f"  {'─'*20}   {'─'*12}")
    print(f"  {'NET WORTH':<20} €{d['net_worth']:>12,.2f}")

    print(f"\n  ── TOTAL MONTHLY SPENDING")
    print(f"  {'Avg/Month':<22} €{d['total_monthly_avg']:>9,.2f}   "
          f"€{d['total_monthly_std']:>8,.2f}   ±€{d['total_monthly_ci']:>9,.2f}   "
          f"(all {len(all_months_str)} months)")

    print(f"\n  ── LAST 12 MONTHS (excluding Jul + Aug) ──")
    print(f"\n  {'Month':<12} {'Total Spent':>12}  {'vs Avg':>10}")
    print(f"  {'─'*12}   {'─'*12}  {'─'*10}")
    for m in active_months:
        total = monthly_totals_active.get(m, 0)
        diff  = total - d["avg_last12"]
        sign  = "▲" if diff > 0 else "▼"
        print(f"  {m:<12} €{total:>10,.2f}  {sign} €{abs(diff):>7,.2f}")

    print(f"\nAvg monthly spend: €{d['avg_last12']:,.2f}  |  "
          f"Std Dev: €{d['std_last12']:,.2f}  |  95% CI: ±€{d['ci_last12']:,.2f}")

    # Utility breakdown
    print(f"\n  ─── Utility breakdown ───")
    print(f"\n  {'Month':<12} {'Electricity':>12}  {'Gas':>12}  {'Water':>12}  {'Phone':>12}")
    print(f"  {'─'*12}   {'─'*12}  {'─'*10}  {'─'*10}  {'─'*10}")
    for m in active_months:
        elec  = d["monthly_electricity"].get(m, 0)
        gas   = d["monthly_gas"].get(m, 0)
        water = d["monthly_water"].get(m, 0)
        phone = d["monthly_phone"].get(m, 0)
        print(f"  {m:<12} €{elec:>10,.2f}  €{gas:>10,.2f}  €{water:>10,.2f}  €{phone:>10,.2f}")

    print(f"\n  {'Utility':<30} {'Total':>10}   {'Avg/Month':>10}   {'Std Dev':>9}   {'95% CI (±)':>11}")
    print(f"  {'─'*30}   {'─'*10}   {'─'*10}   {'─'*9}   {'─'*11}")
    for label, total_key, avg_key, std_key, ci_key in [
        ("⚡ Electricity", "total_electricity" if "total_electricity" in d else None,
         "avg_last12_electricity", "std_last12_electricity", "ci_last12_electricity"),
        ("🔥 Gas",         None, "avg_last12_gas",         "std_last12_gas",         "ci_last12_gas"),
        ("💧 Water",       None, "avg_last12_water",       "std_last12_water",       "ci_last12_water"),
        ("📱 Phone",       None, "avg_last12_phone",       "std_last12_phone",       "ci_last12_phone"),
    ]:
        util_map = {
            "⚡ Electricity": d["monthly_electricity"],
            "🔥 Gas":         d["monthly_gas"],
            "💧 Water":       d["monthly_water"],
            "📱 Phone":       d["monthly_phone"],
        }
        total_val = sum(util_map[label].values())
        avg_val   = d[avg_key]
        std_val   = d[std_key]
        ci_val    = d[ci_key]
        print(f"  {label:<30}  €{total_val:>9,.2f}   €{avg_val:>9.2f}   €{std_val:>8.2f}   ±€{ci_val:>9.2f}")
