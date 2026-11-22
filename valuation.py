"""
valuation.py — What the portfolio is actually WORTH, and what it has returned.

Why this module has to exist
----------------------------
Everything else in this app is derived from transactions, and transactions only
ever tell you how much money you PUT IN. The "invested" figure on the
Investments tab is a cost basis: the sum of every contribution. It is not what
the portfolio is worth, and nothing in a bank export makes it so.

It is worth being precise about why this can't just be automated. Trade
Republic's rows do carry the unit quantity and the ISIN, so in principle units
could be accumulated and priced. But only some banks do: the others report a euro
amount and nothing else. Automating a small share and leaving the rest guessed would be
a worse answer than asking, so this module asks: you record what each holding is
worth, whenever you feel like it, and everything below follows from that.

What follows from it
--------------------
    contributed  — from transactions (what you paid in)
    value        — from your snapshot (what it's worth)
    gain         = value - contributed
    simple return = gain / contributed
    XIRR         — annualised money-weighted return

The last one is the honest number and the reason this module does real work
rather than a subtraction. A simple return divides the total gain by the total
contributed as if every euro had been invested for the same length of time. It
hasn't: contributions are spread over time, and a euro added last
month has had no chance to compound. XIRR solves for the annual rate that makes
the actual dated cash flows balance, so early and late money are weighted for
how long each was actually at work.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

VALUES_FILE = Path(__file__).parent / "fund_values.json"


# ── Snapshot storage ──────────────────────────────────────────────────────────
# Keyed by month ("2026-09") because every other time series in this app is
# monthly, which keeps snapshots joinable with contributions without any
# date-alignment fuss. One entry per fund per month.

def load_snapshots() -> dict[str, dict[str, float]]:
    """{"2026-09": {"Fund A": 1234.5, ...}, ...} — oldest-first ordering is not
    guaranteed by JSON, so callers that care should sort the keys."""
    if not VALUES_FILE.exists():
        return {}
    try:
        with open(VALUES_FILE, encoding="utf-8") as f:
            raw = json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}
    # be forgiving about hand-edited files
    clean: dict[str, dict[str, float]] = {}
    for month, funds in (raw or {}).items():
        if not isinstance(funds, dict):
            continue
        vals = {}
        for name, v in funds.items():
            try:
                vals[str(name)] = float(v)
            except (TypeError, ValueError):
                continue
        if vals:
            clean[str(month)] = vals
    return clean


def save_snapshot(month: str, values: dict[str, float]) -> None:
    """Record (or replace) one month's valuation. Zero/blank entries are dropped
    rather than stored, so a fund you left empty doesn't get recorded as being
    worth nothing."""
    snaps = load_snapshots()
    kept = {k: float(v) for k, v in values.items()
            if v is not None and float(v) != 0.0}
    if kept:
        snaps[month] = kept
    else:
        snaps.pop(month, None)
    with open(VALUES_FILE, "w", encoding="utf-8") as f:
        json.dump(dict(sorted(snaps.items())), f, ensure_ascii=False, indent=2)


def delete_snapshot(month: str) -> None:
    snaps = load_snapshots()
    if snaps.pop(month, None) is not None:
        with open(VALUES_FILE, "w", encoding="utf-8") as f:
            json.dump(dict(sorted(snaps.items())), f, ensure_ascii=False, indent=2)


def latest_snapshot(snaps: dict | None = None) -> tuple[str | None, dict[str, float]]:
    """The most recent valuation on record, as (month, {fund: value})."""
    snaps = load_snapshots() if snaps is None else snaps
    if not snaps:
        return None, {}
    month = max(snaps)
    return month, dict(snaps[month])


# ── XIRR ──────────────────────────────────────────────────────────────────────

def _npv(rate: float, flows: list[tuple[date, float]], t0: date) -> float:
    total = 0.0
    for when, amount in flows:
        years = (when - t0).days / 365.0
        total += amount / (1.0 + rate) ** years
    return total


def xirr(flows: list[tuple[date, float]],
         lo: float = -0.9999, hi: float = 10.0) -> float | None:
    """
    Annualised money-weighted return for dated cash flows.

    `flows` uses the sign convention of the investor's pocket: contributions are
    negative (money leaving you), the closing valuation is positive. Returns the
    rate where the discounted flows net to zero, or None when that question has
    no answer.

    Bisection rather than Newton-Raphson on purpose. Newton converges faster but
    can diverge or land outside (-1, ∞) on the short, lumpy flow sequences this
    app produces, and a silently wrong return percentage is far worse than a
    blank one. Bisection cannot diverge: given a sign change it always converges,
    just less quickly, and at these sizes "less quickly" is microseconds.
    """
    flows = [(d, float(a)) for d, a in flows if a]
    if len(flows) < 2:
        return None
    # A root requires money both in and out; all-negative or all-positive
    # flows describe no return at all.
    if not (any(a < 0 for _, a in flows) and any(a > 0 for _, a in flows)):
        return None

    flows.sort(key=lambda x: x[0])
    t0 = flows[0][0]
    if all(d == t0 for d, _ in flows):
        return None                       # everything on one day: no time base

    f_lo, f_hi = _npv(lo, flows, t0), _npv(hi, flows, t0)
    if f_lo * f_hi > 0:
        return None                       # no sign change in the search range

    for _ in range(200):
        mid = (lo + hi) / 2.0
        f_mid = _npv(mid, flows, t0)
        if abs(f_mid) < 1e-9 or (hi - lo) < 1e-12:
            return mid
        if f_lo * f_mid < 0:
            hi = mid
        else:
            lo, f_lo = mid, f_mid
    return (lo + hi) / 2.0


# ── Putting contributions and valuations together ─────────────────────────────

def _valuation_date(month: str) -> date:
    """Value a snapshot at the last day of its month — or today, if that month
    hasn't finished yet, so a mid-month snapshot isn't discounted into the
    future (which would quietly inflate XIRR)."""
    y, m = (int(x) for x in month.split("-"))
    last_day = (date(y + (m == 12), (m % 12) + 1, 1) - __import__("datetime").timedelta(days=1))
    today = date.today()
    return min(last_day, today) if (y, m) == (today.year, today.month) else last_day


def fund_cashflows(rows: list[tuple], detect_fund) -> dict[str, list[tuple[date, float]]]:
    """
    Per-fund contribution history as dated cash flows, signed from your pocket:
    money into a fund is negative, a redemption coming back is positive.

    `detect_fund` is processor._detect_investment_fund, passed in rather than
    imported so this module stays independent of processor's import graph.
    """
    out: dict[str, list[tuple[date, float]]] = {}
    for dt, amount, desc, cat, source in rows:
        if cat != "Investments":
            continue
        fund_name, _ = detect_fund(desc, source)
        when = dt.date() if hasattr(dt, "date") else dt
        # In this data a contribution is stored negative (money left the
        # account), which already matches the XIRR convention.
        out.setdefault(fund_name, []).append((when, float(amount)))
    return out


def compute_returns(rows: list[tuple], detect_fund,
                    snapshot_month: str | None = None) -> dict:
    """
    Combine contributions with the recorded valuation into a per-fund and total
    return picture. Returns {"month", "funds": [...], "totals": {...}} — or
    {"month": None, ...} when nothing has been valued yet, which the UI treats
    as "ask for a snapshot" rather than as an error.
    """
    snaps = load_snapshots()
    if snapshot_month is None:
        snapshot_month, values = latest_snapshot(snaps)
    else:
        values = dict(snaps.get(snapshot_month, {}))
    if not snapshot_month or not values:
        return {"month": None, "funds": [], "totals": {}}

    as_of = _valuation_date(snapshot_month)
    flows = fund_cashflows(rows, detect_fund)

    funds = []
    for name in sorted(set(flows) | set(values)):
        cfs = [(d, a) for d, a in flows.get(name, []) if d <= as_of]
        contributed = -sum(a for _, a in cfs)          # positive = money in
        value = float(values.get(name, 0.0))
        gain = value - contributed
        simple = (gain / contributed) if contributed > 0 else None
        rate = xirr(cfs + [(as_of, value)]) if (cfs and value > 0) else None
        funds.append({
            "fund": name,
            "contributed": contributed,
            "value": value,
            "gain": gain,
            "simple_return": simple,
            "xirr": rate,
            "valued": name in values,
        })

    funds.sort(key=lambda f: -f["value"])
    all_cfs = [cf for name in flows for cf in flows[name] if cf[0] <= as_of]
    total_contrib = -sum(a for _, a in all_cfs)
    total_value = sum(f["value"] for f in funds)
    total_gain = total_value - total_contrib
    totals = {
        "contributed": total_contrib,
        "value": total_value,
        "gain": total_gain,
        "simple_return": (total_gain / total_contrib) if total_contrib > 0 else None,
        "xirr": xirr(all_cfs + [(as_of, total_value)]) if (all_cfs and total_value > 0) else None,
        "as_of": as_of,
    }
    return {"month": snapshot_month, "funds": funds, "totals": totals}


def value_timeline(rows: list[tuple], detect_fund) -> list[dict]:
    """
    Month-by-month cost basis, with the recorded valuation wherever one exists.

    Contributions are known for every month — they come from transactions. Value
    is only known for months you actually recorded a snapshot in, so `value` is
    None elsewhere rather than interpolated or carried forward. Carrying the last
    figure forward would draw a flat line through months the portfolio certainly
    did move, inventing a fact; leaving a gap says "not measured", which is true.

    Returns oldest-first: [{month, contributed, value, gain}, ...].
    """
    snaps = load_snapshots()
    contribs: list[tuple[str, float]] = []
    for dt, amount, desc, cat, source in rows:
        if cat != "Investments":
            continue
        when = dt.date() if hasattr(dt, "date") else dt
        contribs.append((f"{when.year:04d}-{when.month:02d}", -float(amount)))
    if not contribs and not snaps:
        return []

    months = sorted({m for m, _ in contribs} | set(snaps))
    if not months:
        return []
    # fill the gaps so the cost-basis line is continuous month to month
    y0, m0 = (int(x) for x in months[0].split("-"))
    y1, m1 = (int(x) for x in months[-1].split("-"))
    all_months = []
    y, m = y0, m0
    while (y, m) <= (y1, m1):
        all_months.append(f"{y:04d}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)

    per_month: dict[str, float] = {}
    for mk, amt in contribs:
        per_month[mk] = per_month.get(mk, 0.0) + amt

    out, running = [], 0.0
    for mk in all_months:
        running += per_month.get(mk, 0.0)
        value = sum(snaps[mk].values()) if mk in snaps else None
        out.append({
            "month": mk,
            "contributed": round(running, 2),
            "value": None if value is None else round(value, 2),
            "gain": None if value is None else round(value - running, 2),
        })
    return out
