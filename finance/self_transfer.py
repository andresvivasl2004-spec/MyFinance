"""
self_transfer.py — Detect transfers between the user's own accounts by
matching reciprocal transactions across sources, instead of relying on
description keywords.

A self-transfer has a signature that's independent of wording: the same
amount leaves one of your accounts and arrives in another within a few days.
That pairing (opposite sign, equal magnitude, different source, close date)
is a much more reliable signal than a keyword like "myinv" ever appearing
in the description text.
"""

from datetime import datetime, timedelta

# How many days apart the two legs of a transfer can settle and still count
# as a match (bank processing lag between accounts).
DATE_WINDOW_DAYS = 5

# Tolerance for float comparison of amounts.
AMOUNT_EPSILON = 0.01


def find_transfer_match(
    row: dict,
    source: str,
    candidates: list[tuple],
    date_window_days: int = DATE_WINDOW_DAYS,
) -> bool:
    """
    Check whether `row` (a transaction being imported) has a reciprocal
    counterpart already present in `candidates` — i.e. the opposite amount,
    from a different account, within a few days.

    Parameters
    ----------
    row              : {"date": "YYYY-MM-DD", "amount": float, "description": str}
                       — a single row from the file currently being imported.
    source           : Source label of `row` (the account being imported).
    candidates       : Existing transactions to match against — list of
                       (datetime, float, str, str, str) tuples
                       (date, amount, description, category, source_name),
                       typically storage.load_from_global().
    date_window_days : Max days apart the two legs may be and still match.

    Returns
    -------
    True if a reciprocal transaction (opposite sign, same absolute amount,
    different source, within the date window) exists among `candidates`.
    """
    row_date = datetime.strptime(row["date"], "%Y-%m-%d")
    row_amount = row["amount"]

    for cand_date, cand_amount, _desc, _cat, cand_source in candidates:
        if cand_source == source:
            continue
        if abs(cand_amount + row_amount) > AMOUNT_EPSILON:
            continue
        if abs((cand_date - row_date).days) > date_window_days:
            continue
        return True
    return False
