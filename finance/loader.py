"""
loader.py — Load and validate bank CSV files into a unified transaction list.

Each CSV must have exactly these four columns:
    Date, Amount, Description, Category

Dates must be ISO format: YYYY-MM-DD
Amounts are signed floats: negative = expense, positive = income/transfer.
"""

import os
import pandas as pd
from datetime import datetime


# Required columns in every CSV
REQUIRED_COLUMNS = {"Date", "Amount", "Description", "Category"}

# Transaction tuple field order (also used as a named reference in comments)
# (datetime, float, str, str, str) → (date, amount, description, category, source)


def load_account(path: str, source_name: str) -> list[tuple]:
    """
    Load a single bank CSV and return a list of transaction tuples.

    Parameters
    ----------
    path        : Path to the CSV file.
    source_name : Label for this account (e.g. "BBVA", "MyInvestor").

    Returns
    -------
    List of (datetime, float, str, str, str) tuples:
        (date, amount, description, category, source_name)
    Returns an empty list if the file is missing (with a warning).
    """
    if not os.path.exists(path):
        print(f"  ⚠️  File not found: {path!r}  — skipping {source_name}")
        return []

    df = pd.read_csv(path)
    df["Date"] = pd.to_datetime(df["Date"])   # YYYY-MM-DD — unambiguous

    missing = REQUIRED_COLUMNS - set(df.columns)
    if missing:
        raise ValueError(f"{path}: missing columns {missing}")

    rows = [
        (
            row["Date"].to_pydatetime(),
            float(row["Amount"]),
            str(row["Description"]),
            str(row["Category"]),
            source_name,
        )
        for _, row in df.iterrows()
    ]
    print(f"  ✅ {source_name:<20} {len(rows):>5} rows  ({path})")
    return rows


def load_accounts(csv_files: dict[str, str]) -> list[tuple]:
    """
    Load multiple bank CSVs and merge them into one transaction list.

    Parameters
    ----------
    csv_files : Dict mapping source_name → file_path.
                Example: {"BBVA": "bbva_transactions.csv", "MyInvestor": "myinvestor.csv"}

    Returns
    -------
    Combined list of (datetime, float, str, str, str) tuples, sorted by date descending.
    """
    print("Loading CSVs…")
    all_rows = []
    for source_name, path in csv_files.items():
        all_rows.extend(load_account(path, source_name))

    all_rows.sort(key=lambda r: r[0], reverse=True)
    print(f"  Total rows loaded: {len(all_rows)}")
    return all_rows


def preview(all_data: list[tuple], n: int = 10) -> pd.DataFrame:
    """
    Return the first n transactions as a readable DataFrame.
    Useful for a quick sanity check in the notebook.
    """
    rows = [
        (dt.strftime("%Y-%m-%d"), amt, desc, cat, src)
        for dt, amt, desc, cat, src in all_data[:n]
    ]
    return pd.DataFrame(rows, columns=["Date", "Amount", "Description", "Category", "Source"])
