"""
processor.py — Aggregate transactions into monthly totals, stats, and summaries.

Call `process(all_data)` to get back a single dict with every computed value
that the charts and summary printer need.
"""

import numpy as np
from collections import defaultdict, Counter
from datetime import datetime

from .investments import get_classification


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
# ── Fund registry ─────────────────────────────────────────────────────────────
# PLACEHOLDER LIST. These are illustrative examples, not anyone's real holdings.
# Replace them with the fund names exactly as they appear in your own bank or
# broker exports; nothing else in the app needs to change.
#
# Each entry maps one canonical fund name to (a list of keywords that may appear
# in an export row, the provider that holds it). Several keywords can point at
# the SAME canonical name on purpose: providers rename their export labels from
# time to time, and if the old and new label map to two different names, one
# position silently splits into two and each half understates its real weight.
INVESTMENT_FUNDS = {
    "Global Equity ETF (Acc)":   (["GLOBAL EQUITY ETF ACC"],       "Broker One"),
    "Physical Gold ETC":         (["PHYSICAL GOLD ETC"],           "Broker One"),
    "Developed World Index":     (["DEVELOPED WORLD INDEX",
                                   "DEVELOPED WRLD IDX"],          "Broker Two"),
    # The renamed-label case: same fund, two spellings across time.
    "World Index Fund Dis EUR":  (["WORLD INDEX FUND DIS",
                                   "WORLD INDEX FD DIS EUR"],      "Broker Two"),
    "Sustainable 500 Index Acc": (["SUSTAINABLE 500 INDEX",
                                   "SUSTAIN 500 IDX ACC"],         "Broker Two"),
    "Value Equity Fund D":       (["VALUE EQUITY FUND",
                                   "VALUE EQ FUND D"],             "Broker Two"),
    "Bank Managed Funds":        (["BANK MANAGED FUND"],           "Bank Three"),
}

# ── Asset class per fund ───────────────────────────────────────────────────────
# "Equity", "Fixed income" (bonds/deposits), or "Commodities". BBVA's export
# only ever gives a generic "Contributions to investment funds" line with no
# fund name, so its actual holding can't be classified from the data alone
# — it's left unclassified until the user sets it from the Investments tab.
FUND_ASSET_CLASS = {
    "Global Equity ETF (Acc)":   "Equity",
    "Physical Gold ETC":         "Commodities",
    "Developed World Index":     "Equity",
    "World Index Fund Dis EUR":  "Equity",
    "Sustainable 500 Index Acc": "Equity",
    "Value Equity Fund D":       "Equity",
    "Bank Managed Funds":        "Fixed income",
    "Other Investment":          "Unspecified",
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


def _detect_investment_fund(description: str, source: str | None = None) -> tuple[str, str]:
    """Returns (fund_name, account_name).

    BBVA's export never names the specific fund (every contribution reads
    something generic like "Investment funds - debit subscriptions:
    Contributions to investment funds"), so the keyword match above can
    never succeed for it. Falling back on the transaction's real source
    lets a BBVA investment still be labelled "BBVA Investment Funds"
    (known to be Fixed income) instead of being lumped into the catch-all
    "Other Investment" bucket alongside genuinely unidentified funds from
    other banks.
    """
    desc_upper = description.upper()
    for fund_name, (keywords, account) in INVESTMENT_FUNDS.items():
        if any(kw in desc_upper for kw in keywords):
            return fund_name, account
    if source == "BBVA":
        return "BBVA Investment Funds", "BBVA"
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
    # Keyed by (fund_name, source) — the real account each transaction came
    # from, NOT a hardcoded fund→bank guess. A generic/unidentified fund
    # (e.g. "Other Investment") can legitimately come from more than one
    # bank, so this is the only way to attribute it correctly.
    fund_source_totals    = defaultdict(float)
    investment_txn_count  = defaultdict(int)
    investment_first_date: dict = {}
    investment_last_date:  dict = {}

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
        if amount >= 0 and cat != "Investments":
            continue   # skip positive amounts that aren't income/interest —
            # except Investments: a positive amount there is a partial
            # redemption/rebate from a fund, not spending. It still has to be
            # netted against that fund's total below, or the money vanishes
            # from the books entirely (it's excluded from "cash if never
            # invested" for being Investments-category, and if it's also
            # skipped here it never reduces account_totals either — so it
            # never comes back as cash anywhere, understating your real
            # balance by exactly that amount).

        if amount >= 0:
            # cat == "Investments" and amount >= 0: a redemption/rebate.
            # Net it against the fund's total but don't count it as money
            # spent this month (monthly_by_cat/monthly_investments track
            # outflows into investments, not money coming back).
            fund_name, _ = _detect_investment_fund(desc, source)
            key = (fund_name, source)
            fund_source_totals[key] -= amount
            investment_txn_count[key] += 1
            if key not in investment_first_date or dt < investment_first_date[key]:
                investment_first_date[key] = dt
            if key not in investment_last_date or dt > investment_last_date[key]:
                investment_last_date[key] = dt
            continue

        abs_amt = abs(amount)
        monthly_by_cat[mk][cat] += abs_amt

        if cat == "Groceries":
            monthly_grocery[mk] += abs_amt
            merchant = _detect_grocery_merchant(desc)
            grocery_detail[mk][merchant] += abs_amt
        elif cat == "Investments":
            monthly_investments[mk] += abs_amt
            fund_name, _ = _detect_investment_fund(desc, source)
            investments_detail[mk][fund_name] += abs_amt
            key = (fund_name, source)
            fund_source_totals[key] += abs_amt
            investment_txn_count[key] += 1
            if key not in investment_first_date or dt < investment_first_date[key]:
                investment_first_date[key] = dt
            if key not in investment_last_date or dt > investment_last_date[key]:
                investment_last_date[key] = dt
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

    # ── Cash by bank (part 1: net flow if nothing had ever been invested) ────
    # Unlike every total above, this needs Self-transfers INCLUDED (with
    # their real sign) — a self-transfer is exactly what moves cash from one
    # of your banks to another, so excluding it (as the main loop does, to
    # avoid double-counting the grand total) would misattribute cash between
    # banks even though the overall total stays correct. Investment
    # transactions are excluded here too (handled in part 2 below, once
    # account_totals — how much was actually invested from each bank — is
    # known).
    _bank_flow_excl_investments: dict[str, float] = defaultdict(float)
    for dt, amount, desc, cat, source in all_data:
        if cat == "Investments":
            continue
        _bank_flow_excl_investments[source] += amount

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
    # total_spending excludes Investments: moving cash into a fund isn't a
    # loss, it's converting cash into another asset of equal value.
    total_spending    = sum(cat_totals.values()) - total_investments
    # For the same reason, investments must NOT be added back in as an
    # "inflow" here — that would count the same money twice (once by
    # excluding it from total_spending, once by adding it to total_inflows).
    # Investing is net-worth-neutral: cash out, fund shares in.
    total_inflows     = total_income + total_interest
    net_worth         = total_inflows - total_spending

    # ── Net worth composition: how much of it sits as cash vs. invested ──────
    # total_investments is the cumulative amount ever moved into investment
    # funds/ETFs (contributions only — the app doesn't track sells or market
    # appreciation), so it doubles as "how much of your net worth currently
    # sits in your investment accounts". Whatever's left of net_worth is cash
    # sitting in your regular bank accounts.
    cash_worth = net_worth - total_investments
    if net_worth:
        cash_pct       = cash_worth / net_worth * 100
        invested_pct   = total_investments / net_worth * 100
    else:
        cash_pct       = 0.0
        invested_pct   = 0.0

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
    # One row per (fund, real source account) pair, sorted by amount desc.
    # Using the transaction's actual Source (rather than a hardcoded
    # fund→bank guess) means an unidentified fund that happens to have been
    # bought from more than one bank still gets split and attributed
    # correctly instead of being lumped under a single "Unknown" bucket.
    _fund_source_pairs = sorted(fund_source_totals.items(), key=lambda kv: -kv[1])

    fund_names         = [k[0] for k, _ in _fund_source_pairs]
    fund_accounts      = [k[1] for k, _ in _fund_source_pairs]
    # A (fund, source) position can come out negative when the selected date
    # window contains a redemption/rebate but not the earlier contribution(s)
    # it nets against (e.g. filtering to just the last couple of months when
    # the fund was funded well before that and partially redeemed inside the
    # window). There's no meaningful "money currently in this fund, counting
    # only this window" in that case, and a negative wedge crashes the pie
    # chart (matplotlib requires non-negative sizes). Floor at 0 — the fund
    # just shows as having nothing allocated to it within that window, which
    # is the closest sane answer without silently reaching outside the range
    # the user actually selected.
    fund_amounts       = [max(amt, 0.0) for _, amt in _fund_source_pairs]
    fund_txn_counts    = [investment_txn_count[k] for k, _ in _fund_source_pairs]
    fund_first_dates   = [investment_first_date[k] for k, _ in _fund_source_pairs]
    fund_last_dates    = [investment_last_date[k] for k, _ in _fund_source_pairs]

    # Asset class / interest type: the user's own classification (set from
    # the Investments tab) always wins over the hardcoded FUND_ASSET_CLASS
    # guess.
    fund_asset_classes  = []
    fund_interest_types = []
    for fund_name in fund_names:
        default_ac = FUND_ASSET_CLASS.get(fund_name, "Unspecified")
        ac, interest_type = get_classification(fund_name, default_asset_class=default_ac)
        fund_asset_classes.append(ac)
        fund_interest_types.append(interest_type)

    total_invested = sum(fund_amounts)

    account_totals: Counter = Counter()
    for acc, amt in zip(fund_accounts, fund_amounts):
        account_totals[acc] += amt

    # ── Cash by bank (part 2) ──────────────────────────────────────────────────
    # _bank_flow_excl_investments[X] is what bank X's balance would be if you
    # had never invested any of it. Since you did, that money actually left
    # X's cash and became the invested position tracked in account_totals[X]
    # — subtract it to get the real remaining cash balance per bank.
    cash_by_bank: dict[str, float] = {
        src: _bank_flow_excl_investments.get(src, 0.0) - account_totals.get(src, 0.0)
        for src in set(_bank_flow_excl_investments) | set(account_totals)
    }

    asset_class_totals: Counter = Counter()
    for ac, amt in zip(fund_asset_classes, fund_amounts):
        asset_class_totals[ac] += amt

    # ── Merchant totals (grocery) ─────────────────────────────────────────────
    merchant_totals: dict[str, float] = defaultdict(float)
    for mk, merchants in grocery_detail.items():
        for merch, val in merchants.items():
            merchant_totals[merch] += val

    return {
        # Raw monthly dicts
        # (converted to plain dicts — a defaultdict with a lambda default_factory
        #  can't be pickled, which is required by Streamlit's @st.cache_data)
        "monthly_by_cat":       {m: dict(cats) for m, cats in monthly_by_cat.items()},
        "monthly_grocery":      dict(monthly_grocery),
        "monthly_dining":       dict(monthly_dining),
        "monthly_electricity":  dict(monthly_electricity),
        "monthly_gas":          dict(monthly_gas),
        "monthly_water":        dict(monthly_water),
        "monthly_phone":        dict(monthly_phone),
        "monthly_investments":  dict(monthly_investments),
        "monthly_income":       dict(monthly_income),
        "monthly_interest":     dict(monthly_interest),
        "grocery_detail":       {m: dict(g) for m, g in grocery_detail.items()},
        "investments_detail":   {m: dict(i) for m, i in investments_detail.items()},
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
        "cash_worth":           cash_worth,
        "cash_by_bank":         dict(cash_by_bank),
        "cash_pct":             cash_pct,
        "invested_pct":         invested_pct,
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
        "fund_names":          fund_names,
        "fund_amounts":        fund_amounts,
        "fund_accounts":       fund_accounts,
        "fund_asset_classes":  fund_asset_classes,
        "fund_interest_types": fund_interest_types,
        "fund_txn_counts":     fund_txn_counts,
        "fund_first_dates":    fund_first_dates,
        "fund_last_dates":     fund_last_dates,
        "total_invested":      total_invested,
        "account_totals":      dict(account_totals),
        "asset_class_totals":  dict(asset_class_totals),
        "merchant_totals":     dict(merchant_totals),
    }


# ── Net worth over time ────────────────────────────────────────────────────────

def _iter_fund_snapshots(rows_sorted: list[tuple], months: list[str]):
    """
    Shared engine for every "running balance over time" view (net worth,
    allocation-by-asset-class). Yields (month, fund_source_totals) for each
    month in `months`, where fund_source_totals is the cumulative,
    redemption-netted, floored-at-0 {(fund_name, source): amount} snapshot as
    of the END of that month.

    This is the SAME formula process() uses for a single period (see its
    "amount >= 0 and cat != Investments" branch for the reasoning on
    redemption netting, and its fund_amounts comment for the floor) — kept
    in exactly one place so every timeline view agrees with process() and
    with each other. `rows_sorted` must already be sorted by date; `months`
    must be the sorted list of every "YYYY-MM" that appears in it (or a
    subset of the tail of it — each call only ever advances forward).

    Parameters
    ----------
    rows_sorted : all_data, sorted by date ascending.
    months      : Sorted "YYYY-MM" checkpoints to snapshot at.

    Yields
    ------
    (month: str, fund_source_totals: dict[(str, str), float])
    """
    fund_source_totals: dict = defaultdict(float)
    idx, n = 0, len(rows_sorted)

    for mk in months:
        while idx < n and rows_sorted[idx][0].strftime("%Y-%m") <= mk:
            dt, amount, desc, cat, source = rows_sorted[idx]
            if cat == "Investments":
                fund_name, _ = _detect_investment_fund(desc, source)
                key = (fund_name, source)
                if amount >= 0:
                    fund_source_totals[key] -= amount   # redemption/rebate
                else:
                    fund_source_totals[key] += abs(amount)
            idx += 1

        yield mk, {k: max(v, 0.0) for k, v in fund_source_totals.items()}


def compute_net_worth_timeline(all_data: list[tuple]) -> list[dict]:
    """
    Net worth, split into cash vs. invested, as of the END of every month
    that has at least one transaction.

    This is a RUNNING BALANCE, not a period flow — each month's snapshot is
    built from every transaction from the very beginning up through that
    month, always. That's deliberate and different from process(): a
    start_month cutoff (like the sidebar's Period filter uses) would lop off
    real history and make the series start from a wrong, non-zero baseline,
    and — as we learned from the Investments pie chart crash — a window that
    contains a redemption without the contribution it nets against can drive
    a fund's tracked total negative. Neither problem can happen here, because
    every snapshot's "window" always starts at the true beginning.

    Uses the same cash-per-bank and fund-redemption-netting formulas as
    process() (see there for the reasoning), just computed incrementally in
    one pass instead of one process() call per month, so the two stay in
    agreement and this stays cheap even over years of history.

    Parameters
    ----------
    all_data : Output of loader/storage — list of
               (datetime, float, str, str, str) tuples.

    Returns
    -------
    List of dicts, oldest month first:
        {"month": "YYYY-MM", "cash": float, "invested": float, "net_worth": float}
    """
    if not all_data:
        return []

    rows = sorted(all_data, key=lambda r: r[0])
    months = sorted({dt.strftime("%Y-%m") for dt, *_ in rows})

    bank_flow_excl_investments: dict = defaultdict(float)
    idx, n = 0, len(rows)

    timeline: list[dict] = []
    for mk, fund_totals in _iter_fund_snapshots(rows, months):
        # Advance the bank-flow accumulation to the same month boundary.
        # Self-transfers count here (they move cash between your own
        # accounts, with their real sign) but Investments-category rows
        # never do — see process()'s "_bank_flow_excl_investments" comment.
        while idx < n and rows[idx][0].strftime("%Y-%m") <= mk:
            dt, amount, desc, cat, source = rows[idx]
            if cat != "Investments":
                bank_flow_excl_investments[source] += amount
            idx += 1

        invested_by_source: dict = defaultdict(float)
        total_invested = 0.0
        for (_, source), amt in fund_totals.items():
            invested_by_source[source] += amt
            total_invested += amt

        total_cash = sum(bank_flow_excl_investments.values()) - sum(invested_by_source.values())

        timeline.append({
            "month":     mk,
            "cash":      total_cash,
            "invested":  total_invested,
            "net_worth": total_cash + total_invested,
        })

    return timeline


def compute_allocation_timeline(all_data: list[tuple]) -> list[dict]:
    """
    Asset-class mix over time: how much of your invested money sits in each
    asset class (Equity, Fixed income, Commodities, Unspecified) as of the
    END of every month that has at least one transaction.

    Same running-balance semantics as compute_net_worth_timeline() — every
    snapshot uses the complete history from the beginning, never a narrower
    window — for the same reasons (see that function's docstring). Respects
    the user's manual per-fund classification overrides the same way
    process() does (get_classification() wins over the FUND_ASSET_CLASS
    guess), so this always agrees with what the Investments tab shows for
    "now".

    Parameters
    ----------
    all_data : Output of loader/storage — list of
               (datetime, float, str, str, str) tuples.

    Returns
    -------
    List of dicts, oldest month first:
        {"month": "YYYY-MM", "by_class": {"Equity": float, ...}, "total": float}
    """
    if not all_data:
        return []

    rows = sorted(all_data, key=lambda r: r[0])
    months = sorted({dt.strftime("%Y-%m") for dt, *_ in rows})

    # Fund → asset class is a stable lookup independent of the running
    # totals, so it only needs computing once per fund, not once per month.
    _class_cache: dict[str, str] = {}

    def _asset_class(fund_name: str) -> str:
        if fund_name not in _class_cache:
            default_ac = FUND_ASSET_CLASS.get(fund_name, "Unspecified")
            ac, _ = get_classification(fund_name, default_asset_class=default_ac)
            _class_cache[fund_name] = ac
        return _class_cache[fund_name]

    timeline: list[dict] = []
    for mk, fund_totals in _iter_fund_snapshots(rows, months):
        by_class: dict = defaultdict(float)
        for (fund_name, _source), amt in fund_totals.items():
            if amt <= 0:
                continue
            by_class[_asset_class(fund_name)] += amt

        timeline.append({
            "month":    mk,
            "by_class": dict(by_class),
            "total":    sum(by_class.values()),
        })

    return timeline


# ── Calendar view ────────────────────────────────────────────────────────────

def daily_summary(all_data: list[tuple]) -> dict[str, dict]:
    """
    Group every transaction by calendar day, for the Calendar tab.

    Unlike process(), Investments rows are included here with their real
    sign, so a day's "total" reflects literally everything that happened to
    your money that day, not just spending. Self-transfer rows are the one
    exception — excluded entirely (not just netted to zero): a transfer
    between two of your own accounts often posts as two separate legs on two
    different days (the bank takes a day or more to settle it), so showing
    each leg's raw amount on its own day would make an ordinary transfer
    look like a big, unexplained outflow or inflow on days nothing was
    actually spent or earned. Since it's not new money and not spending, it
    doesn't belong in a day-by-day view at all.

    Returns
    -------
    {"YYYY-MM-DD": {"total": float, "transactions": [
        {"date": datetime, "amount": float, "description": str,
         "category": str, "source": str}, ...
    ]}}
    """
    days: dict[str, dict] = {}
    for dt, amount, desc, cat, source in all_data:
        if cat == "Self-transfer":
            continue
        key = dt.strftime("%Y-%m-%d")
        if key not in days:
            days[key] = {"total": 0.0, "transactions": []}
        days[key]["total"] += amount
        days[key]["transactions"].append({
            "date": dt, "amount": amount, "description": desc,
            "category": cat, "source": source,
        })
    return days


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
    print(f"    {'· Cash':<18} €{d['cash_worth']:>12,.2f}   ({d['cash_pct']:.1f}%)")
    print(f"    {'· Invested':<18} €{d['total_investments']:>12,.2f}   ({d['invested_pct']:.1f}%)")

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
