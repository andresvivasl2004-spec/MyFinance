"""
forecast.py — What the coming months look like, and how long the cash lasts.

Why this is a runway model and not a budget projection
------------------------------------------------------
The obvious way to forecast is to average income, average spending, subtract,
and extend the line. With income that arrives in a few
lump sums a year, that reports a comfortable monthly surplus, which is not just
imprecise — it describes a situation that has never happened. Most months bring
in almost nothing and a few bring in a lot. The "average month" is an artefact of the arithmetic; no such
month exists.

Spending is the opposite: a fairly steady monthly total, and
a fixed core (rent, phone, electricity, the investment standing order) that
barely moves at all. So one side of this is highly predictable and the other is
genuinely not, and a model that blurs them together throws away the only
reliable information in the data.

What this module does instead is take the predictable side seriously and refuse
to invent the unpredictable one. It projects spending forward, holds future
lump sums at zero unless you say otherwise, and answers the question that shape
of finances actually poses: how long does the money on hand last, and by when
does the next payment need to arrive. Past lump sums are reported as history —
their sizes and the gaps between them — so you can judge that yourself, which
is the honest division of labour between a model and the person who knows why
the money arrives.

Two runways, not one
--------------------
Contributions to investments are an outflow from cash, so they shorten the
runway — but unlike rent they can be paused, and pausing them is the first
lever anyone actually pulls. Reporting a single runway that silently assumes
you keep investing would understate how much room you have. Both are computed.
"""

from __future__ import annotations

import math
from datetime import date

# The lookback for every rate in here. Twelve complete months covers a full
# annual cycle of seasonal bills (heating, water) without reaching so far back
# that it describes a different life.
MONTHS_BACK = 12

# Flows that are not spending: money coming in, and money moving between your
# own accounts. Investments are deliberately NOT here — they leave the current
# account and have to be counted, just separably.
NOT_SPENDING = {"Income", "Interest", "Self-transfer"}

# Income at or above this is treated as a lump sum rather than as part of the
# monthly trickle. Expressed as a multiple of typical monthly spending, because
# "large" only means anything relative to what you get through in a month.
LUMP_MULTIPLE = 1.0


# ── month helpers ─────────────────────────────────────────────────────────────

def _mk(dt) -> str:
    return f"{dt.year:04d}-{dt.month:02d}"


def complete_months(rows, exclude=()) -> list[str]:
    """Every month in the data except the one currently in progress, and except
    any you have marked unrepresentative.

    The month in progress is always short — a partial month of bills looks like
    a cheap month — and including it would drag every rate in this module down
    by however far through the month you happen to be.

    The exclusions matter here more than anywhere else in the app. A month when
    someone else bought the groceries, or when the card wasn't being used, makes
    the burn rate look lower than it is, and a burn rate that is too low makes
    the runway look longer than it is. That is the one error in a forecast worth
    going out of your way to avoid."""
    today = date.today()
    in_progress = f"{today.year:04d}-{today.month:02d}"
    exclude = set(exclude or ())
    return sorted({_mk(dt) for dt, *_ in rows
                   if _mk(dt) < in_progress and _mk(dt) not in exclude})


def next_months(after: str, n: int) -> list[str]:
    y, m = (int(x) for x in after.split("-"))
    out = []
    for _ in range(n):
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
        out.append(f"{y:04d}-{m:02d}")
    return out


def _mean_sd(vals: list[float]) -> tuple[float, float]:
    if not vals:
        return 0.0, 0.0
    mean = sum(vals) / len(vals)
    if len(vals) < 2:
        return mean, 0.0
    var = sum((v - mean) ** 2 for v in vals) / (len(vals) - 1)
    return mean, math.sqrt(var)


# ── what the money goes on ────────────────────────────────────────────────────

def spending_outlook(rows, months_back: int = MONTHS_BACK, exclude=()) -> dict:
    """
    The spending side of the forecast, measured over the last complete months.

    Every category contributes its mean PER MONTH OF THE WINDOW, including the
    ones that only bill occasionally. That is the correct treatment rather than
    a convenience: gas arrives every second month, so amortising it over
    every month is what it genuinely costs you, whereas modelling it
    as "the full bill in the months it appears" would need a billing calendar this data
    does not contain.

    `living_sd` comes from the spread of actual monthly TOTALS, not from adding
    up each category's own spread. Categories move together — a quiet month is
    quiet across the board — and summing their standard deviations would treat
    them as independent and badly overstate the uncertainty.
    """
    months = complete_months(rows, exclude)[-months_back:]
    if not months:
        return {"months": [], "living_mean": 0.0, "living_sd": 0.0,
                "investing_mean": 0.0, "by_category": [],
                "fixed_mean": 0.0, "variable_mean": 0.0}
    window = set(months)

    per_cat: dict[str, dict[str, float]] = {}
    living_total: dict[str, float] = {m: 0.0 for m in months}
    investing_total: dict[str, float] = {m: 0.0 for m in months}

    for dt, amount, _desc, cat, _src in rows:
        m = _mk(dt)
        if m not in window or cat in NOT_SPENDING:
            continue
        v = abs(float(amount))
        per_cat.setdefault(cat, {})[m] = per_cat.setdefault(cat, {}).get(m, 0.0) + v
        if cat == "Investments":
            investing_total[m] += v
        else:
            living_total[m] += v

    n = len(months)
    by_category = []
    for cat, series in per_cat.items():
        vals = [series.get(m, 0.0) for m in months]
        present = sum(1 for v in vals if v > 0)
        mean, sd = _mean_sd(vals)
        cv = (sd / mean) if mean else 0.0
        # "fixed" means: it turns up every month AND for much the same amount.
        # Both halves matter — groceries appear every month too, but at a
        # coefficient of variation of 0.6 they are not a commitment.
        kind = ("fixed" if present >= n - 1 and cv < 0.35 else
                "regular" if present >= n * 0.66 else
                "occasional" if present >= 3 else "rare")
        by_category.append({"category": cat, "months_present": present,
                            "mean": mean, "sd": sd, "cv": cv, "kind": kind})
    by_category.sort(key=lambda c: -c["mean"])

    living_mean, living_sd = _mean_sd([living_total[m] for m in months])
    investing_mean, _ = _mean_sd([investing_total[m] for m in months])
    fixed = sum(c["mean"] for c in by_category
                if c["kind"] == "fixed" and c["category"] != "Investments")
    return {
        "months": months,
        "living_mean": living_mean,
        "living_sd": living_sd,
        "investing_mean": investing_mean,
        "by_category": by_category,
        "fixed_mean": fixed,
        "variable_mean": max(0.0, living_mean - fixed),
    }


# ── where the money comes from ────────────────────────────────────────────────

def income_profile(rows, months_back: int = 24,
                   typical_month: float | None = None, exclude=()) -> dict:
    """
    Income split into the part worth projecting and the part that isn't.

    The trickle — cashback, small refunds, the odd Bizum — is small, frequent and
    genuinely recurring, so it gets an average. The lump sums are reported as a
    list of what arrived and how long the gaps were, and are NOT extrapolated:
    three or four irregular arrivals is not a series you can fit a rate to, and
    a projection that quietly assumed the next one shows up on schedule would
    be a forecast of the model's optimism rather than of your money.
    """
    months = complete_months(rows, exclude)[-months_back:]
    if not months:
        return {"trickle_per_month": 0.0, "lumps": [], "gaps_days": [],
                "threshold": 0.0}
    window = set(months)
    threshold = max(300.0, (typical_month or 0.0) * LUMP_MULTIPLE)

    trickle: dict[str, float] = {m: 0.0 for m in months}
    lumps = []
    for dt, amount, desc, cat, _src in rows:
        if cat not in ("Income", "Interest"):
            continue
        v = float(amount)
        if v <= 0:
            continue
        if _mk(dt) in window and v < threshold:
            trickle[_mk(dt)] += v
        elif v >= threshold:
            lumps.append({"date": dt.date() if hasattr(dt, "date") else dt,
                          "amount": v, "description": str(desc).strip()})
    lumps.sort(key=lambda l: l["date"])
    gaps = [(b["date"] - a["date"]).days for a, b in zip(lumps, lumps[1:])]
    trickle_mean, _ = _mean_sd(list(trickle.values()))

    # When the next one is "due", on the evidence of the last few gaps. The
    # median of the recent gaps rather than the mean of all of them: early
    # history has a different rhythm, and one 169-day gap would drag a mean
    # somewhere no actual gap has ever been. This is offered as a pattern to
    # judge, not a prediction — nothing downstream projects income from it.
    recent = sorted(gaps[-6:])
    typical = recent[len(recent) // 2] if recent else None
    due = None
    if typical and lumps:
        from datetime import timedelta
        due = lumps[-1]["date"] + timedelta(days=typical)
    return {"trickle_per_month": trickle_mean, "lumps": lumps,
            "gaps_days": gaps, "threshold": threshold,
            "typical_gap_days": typical, "next_due": due,
            "last_lump": lumps[-1] if lumps else None}


# ── the projection itself ─────────────────────────────────────────────────────

def project(cash: float, outlook: dict, months: int = 12,
            monthly_income: float = 0.0, keep_investing: bool = True,
            start_after: str | None = None,
            expected_lumps: dict[str, float] | None = None) -> list[dict]:
    """
    Month-by-month cash balance, with an uncertainty band that widens with time.

    The band is ±1σ of a month's spending, grown by √k over k months rather
    than by k. Each month's overspend or underspend is a fresh draw, and errors
    in opposite directions partly cancel, so uncertainty accumulates as the
    square root of the number of months. Multiplying the monthly σ by k instead
    would assume every month goes wrong in the same direction — a band so wide
    after a year that it says nothing.
    """
    burn = outlook["living_mean"] + (outlook["investing_mean"] if keep_investing else 0.0)
    sd = outlook["living_sd"]
    last = (outlook["months"] or [f"{date.today().year}-{date.today().month:02d}"])[-1]
    expected_lumps = expected_lumps or {}

    out, balance = [], float(cash)
    for k, m in enumerate(next_months(start_after or last, months), start=1):
        income = monthly_income + float(expected_lumps.get(m, 0.0))
        balance += income - burn
        spread = sd * math.sqrt(k)
        out.append({"month": m, "balance": balance, "lo": balance - spread,
                    "hi": balance + spread, "burn": burn, "income": income})
    return out


def runway(cash: float, outlook: dict, monthly_income: float = 0.0,
           keep_investing: bool = True) -> dict:
    """
    How many months the cash on hand lasts, assuming no further lump sums.

    The optimistic and pessimistic figures solve for where the ±1σ√k band
    crosses zero, not where a flat ±1σ line does. Writing the balance after k
    months as cash − k·net ∓ σ√k and setting it to zero gives a quadratic in
    x = √k:

        net·x² ± σ·x − cash = 0

    which has one positive root. Doing it this way keeps the two bounds
    consistent with the band drawn on the chart; a cruder ±σ adjustment would
    put the numbers and the picture at odds.
    """
    burn = outlook["living_mean"] + (outlook["investing_mean"] if keep_investing else 0.0)
    net = burn - monthly_income
    sd = outlook["living_sd"]
    if net <= 0:
        return {"months": None, "months_lo": None, "months_hi": None,
                "burn": burn, "net": net, "empty_on": None}

    def solve(sign: int) -> float:
        # net·x² + sign·σ·x − cash = 0, positive root
        a, b, c = net, sign * sd, -float(cash)
        disc = b * b - 4 * a * c
        if disc < 0:
            return 0.0
        return max(0.0, ((-b + math.sqrt(disc)) / (2 * a)) ** 2)

    central = float(cash) / net
    pessimistic = solve(+1)      # spending runs above trend
    optimistic = solve(-1)       # spending runs below trend

    today = date.today()
    y, m = today.year, today.month
    whole = int(central)
    for _ in range(whole):
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return {"months": central, "months_lo": pessimistic, "months_hi": optimistic,
            "burn": burn, "net": net, "empty_on": f"{y:04d}-{m:02d}"}


def summarise(rows, cash: float, months: int = 12,
              keep_investing: bool = True, exclude=()) -> dict:
    """
    Everything the Forecast panel needs, in one call.

    The projection starts AFTER the current calendar month, not after the last
    complete one. `cash` is the balance as it stands today, which already has
    this month's spending so far baked into it; beginning the projection with
    this month would charge a second, full month of costs against a balance
    that has already paid part of them.
    """
    outlook = spending_outlook(rows, exclude=exclude)
    income = income_profile(rows, typical_month=outlook["living_mean"],
                            exclude=exclude)
    trickle = income["trickle_per_month"]
    today = date.today()
    this_month = f"{today.year:04d}-{today.month:02d}"
    return {
        "outlook": outlook,
        "income": income,
        "projection": project(cash, outlook, months, trickle, keep_investing,
                              start_after=this_month),
        "runway": runway(cash, outlook, trickle, keep_investing),
        "runway_no_investing": runway(cash, outlook, trickle, False),
        "cash": float(cash),
        "as_of": this_month,
        "excluded": sorted(exclude or ()),
    }
