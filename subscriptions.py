"""
subscriptions.py — Recurring charges, and when one quietly goes up.

The thing worth catching
------------------------
A subscription rarely announces a price rise. It just debits a slightly larger
number, and because it is a number you stopped reading years ago, nothing in
your month feels different. A gym or a phone contract creeps up in
small steps; individually those are a couple
of euros, and collectively they are a standing order you never agreed to.

So this module's job is not really "list my subscriptions" — you know what you
subscribe to. It is to notice the steps.

How a subscription is told apart from a habit
---------------------------------------------
Regular timing alone is not enough, and this is where a naive detector goes
wrong. Buying lunch at the same canteen every month is monthly and repeated,
but it is not a subscription; the giveaway is that the AMOUNT wanders. A real
subscription sits on a plateau: the same figure, repeated, until the day it
steps to a new figure and sits there.

So the test here is structural. Walk the charges in order and group them into
plateaus, allowing a couple of percent of drift within one. A subscription is a
series that a SMALL number of plateaus explains; a habit needs nearly as many
plateaus as it has charges, and is rejected on that basis. It also means the
price history falls out of the detection for free — the plateau boundaries are
exactly the price changes.
"""

from __future__ import annotations

import re
from datetime import date, timedelta

# How far back to look. This feature answers "what do I pay now, and when did
# it change" — so the window has to be long enough to contain a price rise and
# short enough to exclude a previous life. Over a long history rent alone
# can produce many "price changes" that are really a house move, a deposit or a
# double-paid month; over two years it is usually flat, with nothing to report.
LOOKBACK_MONTHS = 24

MIN_CHARGES = 4          # below this there is no pattern to speak of, only noise
PLATEAU_TOL = 0.02       # 2% — absorbs rounding and tiny VAT drift, not a rise
MIN_STEP_PCT = 0.03      # a "price change" worth reporting
MIN_STEP_EUR = 0.50
# Monthly reaches to 45 days, not 38. Bills that leave by card arrive like
# clockwork, but the ones you pay by hand — rent especially — drift by a week or
# two, and a strict 38-day ceiling drops the single largest recurring commitment
# in the data.
MONTHLY = (24, 45)       # median gap in days that counts as monthly
ANNUAL = (330, 400)

# Legal-entity suffixes and payment-processor boilerplate. Stripping these is
# what lets "VODAFONE DEBIT N VODAFONE ESPANA SAU" and "Vodafone Espana S.A."
# land in the same bucket instead of looking like two merchants.
_NOISE = re.compile(
    r"\b(s\s?a\s?u|s\s?a|s\s?l\s?u|s\s?l|sociedad|espana|espanola|"
    r"card payment|debit to account|debit n|transfer completed|"
    r"transacci n con tarjeta|recibo|pago|n mero de|numero de|clientes|"
    # Month names, Spanish and English. Without these the rent never groups:
    # it is paid as "Renta agosto", "Renta julio", … so every single payment
    # looks like a different merchant and the most regular commitment in the
    # data falls straight through the detector.
    r"enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|setiembre|"
    r"octubre|noviembre|diciembre|"
    r"january|february|march|april|may|june|july|august|september|october|"
    r"november|december|mes del|mes de|mes|month)\b"
)

# Single stray letters left behind once digits are stripped ("N 2025036…" -> "n").
_STRAY = re.compile(r"\b[a-z]\b")


def merchant_key(description: str) -> str:
    """A stable bucket for one merchant, tolerant of the noise banks add."""
    d = str(description).lower()
    d = re.sub(r"[^a-z\s]", " ", d)       # drop digits and punctuation entirely
    d = _NOISE.sub(" ", d)
    d = _STRAY.sub(" ", d)
    d = re.sub(r"\s+", " ", d).strip()
    return d[:28]


def display_name(key: str) -> str:
    """What to show a human. The cleaned key, not the rawest description: the
    bank writes "Debit to account: N 0000000000000000 Some Gym", and a list of
    account numbers is no use for recognising what you pay for."""
    return " ".join(w.capitalize() for w in key.split()) or key


def _median(xs):
    xs = sorted(xs)
    return xs[len(xs) // 2] if xs else 0.0


def _plateaus(charges, tol: float = PLATEAU_TOL):
    """Split a dated amount series into runs that sit on the same price.

    Compared against the plateau's running median rather than the previous
    charge, so one odd month (a double bill, a partial refund) doesn't reset the
    baseline and turn a steady price into a staircase.
    """
    out = []
    for when, amount in charges:
        if out:
            level = _median([a for _, a in out[-1]])
            if level and abs(amount - level) / level <= tol:
                out[-1].append((when, amount))
                continue
        out.append([(when, amount)])

    # Merge away temporary dips and spikes: a short plateau whose neighbours
    # agree with each other is a promotion or a one-off correction, not a price
    # change. Without this, two discounted months read as "−80%" immediately
    # followed by "+390%", which is arithmetically true and tells you nothing
    # about what the thing costs.
    merged = []
    i = 0
    while i < len(out):
        if 0 < i < len(out) - 1 and len(out[i]) < 3:
            before = _median([a for _, a in merged[-1]]) if merged else None
            after = _median([a for _, a in out[i + 1]])
            if before and abs(after - before) / before <= tol:
                merged[-1].extend(out[i])         # absorb the blip
                merged[-1].extend(out[i + 1])     # and rejoin the far side
                i += 2
                continue
        merged.append(list(out[i]))
        i += 1
    return merged


def _steps(plateaus):
    """Price changes between consecutive plateaus, ignoring changes too small to
    mean anything."""
    steps = []
    for prev, cur in zip(plateaus, plateaus[1:]):
        a, b = _median([x for _, x in prev]), _median([x for _, x in cur])
        if not a:
            continue
        if abs(b - a) >= MIN_STEP_EUR and abs(b - a) / a >= MIN_STEP_PCT:
            steps.append({"from": a, "to": b, "when": cur[0][0],
                          "pct": (b - a) / a})
    return steps


def detect(rows, min_charges: int = MIN_CHARGES, today: date | None = None) -> list[dict]:
    """
    Every recurring charge the data supports, newest price first.

    Only outgoings are considered, and investments and self-transfers are
    excluded — a monthly standing order into a fund is regular and stable and
    would otherwise dominate this list, but it isn't a subscription and its
    "price" going up is a decision you made.
    """
    today = today or date.today()
    cutoff = _shift(f"{today.year:04d}-{today.month:02d}", -LOOKBACK_MONTHS)
    groups: dict[str, list] = {}
    meta: dict[str, dict] = {}
    for dt, amount, desc, cat, _src in rows:
        if amount >= 0 or cat in ("Investments", "Self-transfer"):
            continue
        if f"{dt.year:04d}-{dt.month:02d}" < cutoff:
            continue
        k = merchant_key(desc)
        if not k:
            continue
        groups.setdefault(k, []).append(
            (dt.date() if hasattr(dt, "date") else dt, abs(float(amount))))
        m = meta.setdefault(k, {"category": cat, "labels": {}})
        m["labels"][str(desc).strip()] = m["labels"].get(str(desc).strip(), 0) + 1

    found = []
    for k, charges in groups.items():
        if len(charges) < min_charges:
            continue
        charges.sort()
        gaps = [(b[0] - a[0]).days for a, b in zip(charges, charges[1:])]
        gaps = [g for g in gaps if g > 0]
        if not gaps:
            continue
        cadence = _median(gaps)
        period = ("monthly" if MONTHLY[0] <= cadence <= MONTHLY[1] else
                  "annual" if ANNUAL[0] <= cadence <= ANNUAL[1] else None)
        if period is None:
            continue

        plateaus = _plateaus(charges)
        # The structural test: a subscription is explained by few plateaus. A
        # habit that merely recurs needs almost one plateau per charge.
        if len(plateaus) > max(3, len(charges) // 3):
            continue

        # The baseline is the first plateau that actually held for more than one
        # charge. A lone opening figure is usually a pro-rated first bill, and
        # measuring a rise of thousands of percent from it would be true and
        # useless.
        settled = [p for p in plateaus if len(p) >= 2]
        base_plateau = settled[0] if settled else plateaus[0]
        baseline = _median([a for _, a in base_plateau])
        current = _median([a for _, a in plateaus[-1]])
        last_date = charges[-1][0]
        per_year = 12 if period == "monthly" else 1

        found.append({
            "merchant": display_name(k),
            "raw": max(meta[k]["labels"], key=meta[k]["labels"].get),
            "key": k,
            "category": meta[k]["category"],
            "charges": len(charges),
            "period": period,
            "cadence_days": cadence,
            "first_seen": charges[0][0],
            "last_seen": last_date,
            "baseline": baseline,
            "current": current,
            "change_pct": ((current - baseline) / baseline) if baseline else None,
            "annual_now": current * per_year,
            "annual_delta": (current - baseline) * per_year,
            "steps": _steps(plateaus),
            # A charge overdue by most of another cycle probably means the
            # subscription ended — worth showing, because a cancelled service
            # you still believe you pay for distorts every forward estimate.
            "lapsed": (today - last_date).days > cadence * 1.8,
            "next_due": last_date + timedelta(days=int(cadence)),
        })

    found.sort(key=lambda s: -s["annual_now"])
    return found


def creep_summary(subs: list[dict]) -> dict:
    """What the price rises add up to across everything still running."""
    live = [s for s in subs if not s["lapsed"]]
    risen = [s for s in live if s["change_pct"] and s["change_pct"] > 0]
    return {
        "n_active": len(live),
        "n_risen": len(risen),
        "annual_now": sum(s["annual_now"] for s in live),
        "annual_extra": sum(s["annual_delta"] for s in risen),
        "worst": max(risen, key=lambda s: s["change_pct"]) if risen else None,
    }


# ── Commitments a merchant key can never find ─────────────────────────────────

def _category_commitments(rows, subs, today: date) -> list[dict]:
    """
    Recurring commitments detected from the CATEGORY rather than the merchant.

    Some obligations are paid with different free text every single month. The
    rent can be "Alquiler julio", "Renta agosto", "Bizum: Mes de julio" and
    "Transfer: MES FEBRERO" — four spellings of one
    standing commitment, and no amount of cleaning will ever group them, because
    there is no merchant in the string at all.

    But the category knows. So the same structural test is applied a second time
    to each category's MONTHLY TOTAL: if a category turns up nearly every month
    and its monthly total sits on a handful of plateaus, that category IS a
    commitment. A flat rent passes; groceries, whose monthly total
    wanders, produce a plateau per month and are rejected
    by exactly the same rule that rejects a lunch habit.

    Anything already found by merchant is skipped, so the phone doesn't appear
    once as Vodafone and again as "Phone".
    """
    covered = {s["category"] for s in subs}
    cutoff = _shift(f"{today.year:04d}-{today.month:02d}", -LOOKBACK_MONTHS)
    per_cat: dict[str, dict[str, float]] = {}
    for dt, amount, _desc, cat, _src in rows:
        if amount >= 0 or cat in ("Investments", "Self-transfer", "Income", "Interest"):
            continue
        mk = f"{dt.year:04d}-{dt.month:02d}"
        if mk < cutoff:
            continue
        per_cat.setdefault(cat, {})
        per_cat[cat][mk] = per_cat[cat].get(mk, 0.0) + abs(float(amount))

    out = []
    for cat, months in per_cat.items():
        if cat in covered or len(months) < MIN_CHARGES:
            continue
        keys = sorted(months)
        # must be near-continuous: a commitment you pay most months
        span = _months_between(keys[0], keys[-1])
        if len(keys) < len(span) * 0.6:
            continue
        series = [(date(int(m[:4]), int(m[5:]), 1), months[m]) for m in keys]
        plateaus = _plateaus(series)
        if len(plateaus) > max(3, len(series) // 3):
            continue
        settled = [p for p in plateaus if len(p) >= 2]
        base = _median([a for _, a in (settled[0] if settled else plateaus[0])])
        current = _median([a for _, a in plateaus[-1]])
        last_month = keys[-1]
        last_seen = date(int(last_month[:4]), int(last_month[5:]), 1)
        out.append({
            "merchant": cat, "raw": f"(all {cat} transactions)", "key": f"cat:{cat}",
            "category": cat, "charges": len(keys), "period": "monthly",
            "cadence_days": 30, "basis": "category",
            "first_seen": series[0][0], "last_seen": last_seen,
            "baseline": base, "current": current,
            "change_pct": ((current - base) / base) if base else None,
            "annual_now": current * 12, "annual_delta": (current - base) * 12,
            "steps": _steps(plateaus),
            "lapsed": last_month < _shift(f"{today.year:04d}-{today.month:02d}", -1),
            "next_due": None,
        })
    return out


def _shift(month: str, delta: int) -> str:
    y, m = int(month[:4]), int(month[5:])
    i = y * 12 + (m - 1) + delta
    return f"{i // 12:04d}-{i % 12 + 1:02d}"


def _months_between(a: str, b: str) -> list[str]:
    out, cur = [], a
    while cur <= b:
        out.append(cur)
        cur = _shift(cur, 1)
    return out


def detect_all(rows, today: date | None = None) -> list[dict]:
    """Merchant-level subscriptions plus category-level commitments, together."""
    today = today or date.today()
    subs = detect(rows, today=today)
    for s in subs:
        s.setdefault("basis", "merchant")
    subs += _category_commitments(rows, subs, today)
    subs.sort(key=lambda s: -s["annual_now"])
    return subs
