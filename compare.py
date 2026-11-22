"""
compare.py — What actually changed between two stretches of time.

Why averages per month and not totals
-------------------------------------
The obvious comparison is "spent on groceries in the last 6 months" against
"spent on groceries in the 6 before". That works only while both windows hold
the same number of months, and in this app they routinely don't: months you
marked unrepresentative are dropped, and one window may lose two while the
other loses none. Comparing totals across a 6-month window and a 4-month one
reports a 33% fall in spending that is really just a shorter window.

So everything here is per month of the window. It answers the same question,
survives uneven windows, and is the only version that stays true once
exclusions are in play.

What it is for
--------------
Not "what do I spend on" — the category chart already answers that, and it will
tell you rent is the biggest line every single month, forever. This answers
"what got more expensive", which is the question with an action attached. A
category can be small and still be the most interesting thing on the page if it
has doubled.
"""

from __future__ import annotations

from datetime import date

# Investments are excluded along with the inflows. Contributing more to a fund
# is not a cost going up — it is cash converted to another asset of equal value,
# which is exactly why processor's total_spending leaves it out too. Including
# it here would put "Investments +137%" at the top of a chart headed "what got
# more expensive", which is the wrong answer to the question being asked. The
# Investments tab covers contributions, and the forecast counts them separately
# as an outflow you can pause.
NOT_SPENDING = {"Income", "Interest", "Self-transfer", "Investments"}


def _mk(dt) -> str:
    return f"{dt.year:04d}-{dt.month:02d}"


def complete_months(rows, exclude=()) -> list[str]:
    """Months with data, minus the one in progress and any you've excluded."""
    today = date.today()
    in_progress = f"{today.year:04d}-{today.month:02d}"
    exclude = set(exclude or ())
    return sorted({_mk(r[0]) for r in rows
                   if _mk(r[0]) < in_progress and _mk(r[0]) not in exclude})


def split_windows(rows, n: int, exclude=()) -> tuple[list[str], list[str]]:
    """The last `n` usable months, and the `n` usable months before those.

    Both windows skip excluded months rather than spanning them, so each holds
    `n` months of real data — which is what makes the two comparable at all.
    """
    months = complete_months(rows, exclude)
    if len(months) < 2:
        return [], []
    recent = months[-n:]
    prior = months[-2 * n:-n] if len(months) > n else []
    return prior, recent


def compare(rows, n: int = 6, exclude=()) -> dict:
    """
    Per-category spend per month in each window, with the movement between them.

    Categories present in only one window are kept, not dropped: something you
    started paying for is exactly as interesting as something that got dearer,
    and a comparison that quietly ignored new categories would miss it.
    """
    prior, recent = split_windows(rows, n, exclude)
    if not recent:
        return {"prior": [], "recent": [], "categories": [], "totals": {}}
    p_set, r_set = set(prior), set(recent)

    p_tot: dict[str, float] = {}
    r_tot: dict[str, float] = {}
    for dt, amount, _desc, cat, _src in rows:
        if cat in NOT_SPENDING or amount >= 0:
            continue
        m, v = _mk(dt), abs(float(amount))
        if m in p_set:
            p_tot[cat] = p_tot.get(cat, 0.0) + v
        elif m in r_set:
            r_tot[cat] = r_tot.get(cat, 0.0) + v

    np_, nr = max(1, len(prior)), max(1, len(recent))
    cats = []
    for cat in sorted(set(p_tot) | set(r_tot)):
        before = p_tot.get(cat, 0.0) / np_
        after = r_tot.get(cat, 0.0) / nr
        delta = after - before
        cats.append({
            "category": cat,
            "before": before,
            "after": after,
            "delta": delta,
            # A percentage against a base of zero is undefined, not infinite.
            # Reported as None so the UI can say "new" instead of printing a
            # number that looks precise and means nothing.
            "pct": (delta / before) if before > 0 else None,
            "is_new": before == 0 and after > 0,
            "stopped": after == 0 and before > 0,
        })
    cats.sort(key=lambda c: -abs(c["delta"]))

    tb = sum(c["before"] for c in cats)
    ta = sum(c["after"] for c in cats)
    return {
        "prior": prior, "recent": recent, "categories": cats,
        "totals": {"before": tb, "after": ta, "delta": ta - tb,
                   "pct": ((ta - tb) / tb) if tb else None},
    }
