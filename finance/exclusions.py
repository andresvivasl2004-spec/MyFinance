"""
exclusions.py — Months you've told the app not to average over.

Some months aren't wrong, they're just not yours to compare against. A month
where someone else paid for the groceries, or one where you weren't using the
card at all, still happened and the transactions in it are real — but including
them in a monthly average answers a question nobody asked. It looks like
a cheap grocery month; it was a month with a different payer.

This is deliberately separate from the anomaly detection in anomaly.py. That
finds months that are statistically unusual and asks you to look at them. This
records months you have already looked at and judged unrepresentative, for
reasons that are never visible in the numbers. A detector cannot know that
someone else bought the groceries.

Excluded months are dropped from the window entirely rather than shown and
skipped, and the window reaches one month further back for each one dropped, so
"the last 8 months" stays 8 months of usable comparison instead of quietly
becoming 6. The chart titles name what was left out, because a statistic with
silent exclusions is worse than no statistic.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import dates

EXCLUSIONS_FILE = Path(__file__).resolve().parent.parent / "data" / "excluded_months.json"


def load_exclusions() -> set[str]:
    """The excluded months as {"2026-02", "2026-07"}. Never raises: a corrupt or
    hand-edited file degrades to "nothing excluded", which is the safe default —
    showing a month you meant to hide is recoverable, silently hiding data
    because a file went bad is not."""
    if not EXCLUSIONS_FILE.exists():
        return set()
    try:
        with open(EXCLUSIONS_FILE, encoding="utf-8") as f:
            raw = json.load(f)
    except (json.JSONDecodeError, OSError):
        return set()
    out = set()
    for m in raw if isinstance(raw, list) else []:
        m = str(m).strip()
        if len(m) == 7 and m[4] == "-" and m[:4].isdigit() and m[5:].isdigit():
            out.add(m)
    return out


def save_exclusions(months) -> None:
    with open(EXCLUSIONS_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(set(months)), f, indent=2)


def exclusions_key() -> tuple:
    """A cheap cache key: the file's mtime and size, like the other data files.
    Hashing the set itself would work too, but this matches how every other
    on-disk input is keyed in app.py and costs one stat() call."""
    try:
        st = EXCLUSIONS_FILE.stat()
        return (st.st_mtime_ns, st.st_size)
    except OSError:
        return (0, 0)


# ── Months that look like a partial import ────────────────────────────────────

def _mk(dt) -> str:
    return f"{dt.year:04d}-{dt.month:02d}"


def suspect_months(rows, thin_share: float = 0.45) -> list[dict]:
    """
    Months whose data looks incomplete, with the reason for each.

    This is the counterpart to the manual exclusions above. Those cover months
    you know were unusual; this finds the ones you would never think to look
    for, because nothing about them stands out until you go counting — a month
    holding four transactions against a typical month's thirty
    is not a frugal month, it is a statement that was never fully
    imported. A month like that sits in every average silently dragging it down.

    Three independent signals, because each catches what the others miss:

      thin      — far fewer transactions than a typical month
      missing   — a commitment that appears in almost every other month (rent,
                  phone) simply isn't there
      silent    — a bank that reports every month either side of this one
                  reported nothing in it

    The month in progress, or the last one if it is only partly imported, is
    never flagged: it is genuinely incomplete and saying so every day would
    train you to ignore the warning.

    Nothing is excluded automatically. These are suggestions, and an
    unexplained-looking month can be perfectly real — the point is to put it in
    front of you, not to decide for you.
    """
    in_progress = dates.in_progress_month(rows)

    per_month: dict[str, list] = {}
    for r in rows:
        m = _mk(r[0])
        if m < in_progress:
            per_month.setdefault(m, []).append(r)
    months = sorted(per_month)
    if len(months) < 6:
        return []

    # Compared against NEIGHBOURING months, not the whole history. Activity
    # can grow a lot over the years, from a handful of
    # transactions a month to dozens, so a single global median calls
    # the entire first year suspicious and buries the one month that genuinely
    # is. The question worth asking is whether a month is thin for its own era.
    counts = {m: len(per_month[m]) for m in months}

    def local_typical(i: int) -> float:
        lo, hi = max(0, i - 6), min(len(months), i + 7)
        near = sorted(counts[months[j]] for j in range(lo, hi) if j != i)
        return near[len(near) // 2] if near else 0

    # categories that turn up in nearly every month are commitments; their
    # absence is evidence, whereas a missing "Travel" means nothing
    cat_months: dict[str, set] = {}
    for m in months:
        for r in per_month[m]:
            cat_months.setdefault(r[3], set()).add(m)
    # Income and Interest are deliberately not commitments. Income can arrive
    # in occasional lump sums, so "no Income this month" is the normal case, not
    # evidence of anything — including it flagged six perfectly complete months.
    commitments = {c for c, ms in cat_months.items()
                   if len(ms) >= len(months) * 0.8
                   and c not in ("Income", "Interest", "Self-transfer")}

    # a source is expected only between its own first and last appearance
    src_span: dict[str, tuple] = {}
    src_months: dict[str, set] = {}
    for m in months:
        for r in per_month[m]:
            src_months.setdefault(r[4], set()).add(m)
    for s, ms in src_months.items():
        src_span[s] = (min(ms), max(ms))

    out = []
    for i, m in enumerate(months):
        reasons = []
        n = counts[m]
        typical = local_typical(i)
        # Only worth flagging when the months around it were actually busy;
        # a quiet month inside a quiet period is just a quiet period.
        if typical >= 10 and n < max(4, typical * thin_share):
            reasons.append(
                f"only {n} transactions — the months around it average {typical:.0f}")
        absent = sorted(c for c in commitments if m not in cat_months[c])
        if absent:
            reasons.append("no " + ", ".join(absent[:3]))
        quiet = sorted(s for s, (lo, hi) in src_span.items()
                       if lo < m < hi and m not in src_months[s])
        if quiet:
            reasons.append("nothing from " + ", ".join(quiet))
        if reasons:
            out.append({"month": m, "n": n, "reasons": reasons})
    return out
