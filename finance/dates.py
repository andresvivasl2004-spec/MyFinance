"""
dates.py — How far the data goes, and which months in it are complete.

Everything that says "now" has to mean the last day the data covers, not the
day the app is opened. Between imports those can be weeks apart, and treating
an old balance as today's, or a month that was only half imported as a cheap
one, makes every figure built on them wrong.
"""

from __future__ import annotations

import calendar
from datetime import date

# A month whose last transaction falls this close to its end counts as complete:
# plenty of months simply have nothing on the 30th.
MONTH_END_GRACE_DAYS = 5


def month_key(dt) -> str:
    return f"{dt.year:04d}-{dt.month:02d}"


def data_as_of(rows, today: date | None = None) -> date:
    """The last day the data covers. Never later than today."""
    today = today or date.today()
    last = max((dt.date() if hasattr(dt, "date") else dt for dt, *_ in rows), default=today)
    return min(last, today)


def in_progress_month(rows, today: date | None = None) -> str:
    """The first month that is not complete, as "YYYY-MM"."""
    today = today or date.today()
    last = data_as_of(rows, today)
    days_left = calendar.monthrange(last.year, last.month)[1] - last.day
    if month_key(last) < month_key(today) and days_left <= MONTH_END_GRACE_DAYS:
        y, m = (last.year + 1, 1) if last.month == 12 else (last.year, last.month + 1)
        return f"{y:04d}-{m:02d}"
    return month_key(last)


def complete_months(rows, exclude=(), today: date | None = None) -> list[str]:
    """Every month with data, minus incomplete ones and any you've excluded."""
    in_progress, exclude = in_progress_month(rows, today), set(exclude or ())
    months = {month_key(dt) for dt, *_ in rows}
    return sorted(m for m in months if m < in_progress and m not in exclude)
