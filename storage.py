"""
storage.py — Manages the global transaction Excel file.

The global file (gastos_global.xlsx) is the single source of truth.
Each time you categorise a new bank export, you append it here.
Duplicate detection ensures running the same month twice never creates double entries.

Typical notebook workflow
-------------------------
    # Step 1 – categorise the raw bank file (categorizer.py)
    out_csv = categorize_csv("mayo.xlsx", bank="bbva")

    # Step 2 – review in the notebook
    df = review(out_csv)
    display(df)           # inspect, edit categories if needed

    # Step 3 – append to global file (this module)
    n = save_to_global(out_csv, source="BBVA")
    print(f"{n} new rows added")

    # Step 4 – reload all_data for analysis
    all_data = load_from_global()
"""

import pandas as pd
from pathlib import Path
from openpyxl import load_workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

GLOBAL_FILE = "gastos_global.xlsx"
SHEET_NAME  = "Transactions"
COLUMNS     = ["Date", "Amount", "Description", "Category", "Source"]

# Deduplication key: these columns together must be unique
DEDUP_COLS  = ["Date", "Amount", "Description", "Source"]


# ─────────────────────────────────────────────────────────────────────────────
# REVIEW
# ─────────────────────────────────────────────────────────────────────────────

def review(csv_path: str) -> pd.DataFrame:
    """
    Load a categorised CSV into a DataFrame for inspection in the notebook.
    Edit the Category column directly in the DataFrame before calling save_to_global.

    Returns a styled DataFrame ready to display.
    """
    df = pd.read_csv(csv_path, dtype={"Date": str})
    df["Amount"] = df["Amount"].astype(float)
    df = df.sort_values("Date", ascending=False).reset_index(drop=True)
    print(f"  {len(df)} transactions loaded from {csv_path!r}")
    print(f"  Review the table below. Edit categories if needed, then call save_to_global().")
    return df


# ─────────────────────────────────────────────────────────────────────────────
# SAVE TO GLOBAL
# ─────────────────────────────────────────────────────────────────────────────

def save_to_global(
    csv_path_or_df,
    source: str,
    global_file: str = GLOBAL_FILE,
) -> int:
    """
    Append new transactions to the global Excel file.

    Parameters
    ----------
    csv_path_or_df : Path to the categorised CSV (str) or a DataFrame already
                     reviewed and edited in the notebook.
    source         : Account label, e.g. "BBVA", "MyInvestor", "Trade Republic".
    global_file    : Path to the global Excel file (created if it doesn't exist).

    Returns
    -------
    Number of new rows actually appended (0 if all were duplicates).
    """
    # Load new rows
    if isinstance(csv_path_or_df, pd.DataFrame):
        new_df = csv_path_or_df.copy()
    else:
        new_df = pd.read_csv(csv_path_or_df, dtype={"Date": str})

    new_df["Amount"] = new_df["Amount"].astype(float)
    new_df["Source"] = source
    new_df = new_df[["Date", "Amount", "Description", "Category", "Source"]]

    global_path = Path(global_file)

    # Load existing data (or start fresh)
    if global_path.exists():
        existing_df = pd.read_excel(global_path, sheet_name=SHEET_NAME, dtype={"Date": str})
        existing_df["Amount"] = existing_df["Amount"].astype(float)
    else:
        existing_df = pd.DataFrame(columns=COLUMNS)

    # Deduplicate: keep only rows not already in the global file
    if len(existing_df) > 0:
        existing_keys = set(
            existing_df[DEDUP_COLS].apply(
                lambda r: (str(r["Date"]), float(r["Amount"]), str(r["Description"]), str(r["Source"])),
                axis=1,
            )
        )
        mask = new_df.apply(
            lambda r: (str(r["Date"]), float(r["Amount"]), str(r["Description"]), str(r["Source"]))
            not in existing_keys,
            axis=1,
        )
        rows_to_add = new_df[mask]
    else:
        rows_to_add = new_df

    n_new = len(rows_to_add)

    if n_new == 0:
        print(f"  ✓ No new rows — all {len(new_df)} transactions were already in {global_file}")
        return 0

    # Append and sort
    combined = pd.concat([existing_df, rows_to_add], ignore_index=True)
    combined = combined.sort_values("Date", ascending=False).reset_index(drop=True)

    # Write back with formatting
    _write_excel(combined, global_path)

    skipped = len(new_df) - n_new
    print(f"\n  ✅ {n_new} new rows added to {global_file}")
    if skipped:
        print(f"  ⏭  {skipped} duplicate(s) skipped")
    print(f"  📊 Total rows in global file: {len(combined)}")
    print(f"  📅 Period: {combined['Date'].min()}  →  {combined['Date'].max()}")

    return n_new


def _write_excel(df: pd.DataFrame, path: Path) -> None:
    """Write DataFrame to Excel with a clean, readable format."""
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name=SHEET_NAME, index=False)
        ws = writer.sheets[SHEET_NAME]

        # Header style
        header_fill = PatternFill("solid", fgColor="1F3864")
        header_font = Font(name="Arial", bold=True, color="FFFFFF", size=10)
        for cell in ws[1]:
            cell.fill      = header_fill
            cell.font      = header_font
            cell.alignment = Alignment(horizontal="center", vertical="center")

        # Alternating row colours
        light = PatternFill("solid", fgColor="EBF0F8")
        normal_font = Font(name="Arial", size=9)
        for i, row in enumerate(ws.iter_rows(min_row=2, max_row=ws.max_row), start=2):
            fill = light if i % 2 == 0 else PatternFill()
            for cell in row:
                cell.fill = fill
                cell.font = normal_font
                cell.alignment = Alignment(horizontal="left")

        # Column widths
        col_widths = {"Date": 13, "Amount": 11, "Description": 50, "Category": 22, "Source": 16}
        for col_idx, col_name in enumerate(df.columns, 1):
            ws.column_dimensions[get_column_letter(col_idx)].width = col_widths.get(col_name, 15)

        # Amount column: number format
        amount_col = df.columns.get_loc("Amount") + 1
        for cell in ws.iter_rows(min_row=2, max_col=amount_col, max_row=ws.max_row,
                                  min_col=amount_col):
            for c in cell:
                c.number_format = '#,##0.00'
                c.alignment = Alignment(horizontal="right")

        # Freeze header row
        ws.freeze_panes = "A2"

        # Auto-filter
        ws.auto_filter.ref = ws.dimensions


# ─────────────────────────────────────────────────────────────────────────────
# LOAD FROM GLOBAL  (replaces load_accounts for the analysis notebook)
# ─────────────────────────────────────────────────────────────────────────────

def load_from_global(global_file: str = GLOBAL_FILE) -> list[tuple]:
    """
    Load all transactions from the global Excel file.
    Returns the same format as loader.load_accounts():
        [(datetime, float, str, str, str), ...]  →  (date, amount, description, category, source)
    """
    from datetime import datetime as dt

    path = Path(global_file)
    if not path.exists():
        raise FileNotFoundError(
            f"Global file '{global_file}' not found. "
            f"Add data first with save_to_global()."
        )

    df = pd.read_excel(path, sheet_name=SHEET_NAME, dtype={"Date": str})
    df["Amount"] = df["Amount"].astype(float)

    rows = []
    for _, row in df.iterrows():
        try:
            date = dt.strptime(str(row["Date"]).strip()[:10], "%Y-%m-%d")
        except ValueError:
            continue
        rows.append((
            date,
            float(row["Amount"]),
            str(row["Description"]),
            str(row["Category"]),
            str(row["Source"]),
        ))

    rows.sort(key=lambda r: r[0], reverse=True)
    print(f"  ✅ {len(rows)} transactions loaded from {global_file}")
    if rows:
        oldest = rows[-1][0].strftime("%Y-%m")
        newest = rows[0][0].strftime("%Y-%m")
        print(f"  📅 Period: {oldest}  →  {newest}")
    return rows


# ─────────────────────────────────────────────────────────────────────────────
# UTILITIES
# ─────────────────────────────────────────────────────────────────────────────

def global_summary(global_file: str = GLOBAL_FILE) -> pd.DataFrame:
    """Print a quick summary of the global file by source and month."""
    path = Path(global_file)
    if not path.exists():
        print(f"  No global file found at '{global_file}'")
        return pd.DataFrame()

    df = pd.read_excel(path, sheet_name=SHEET_NAME, dtype={"Date": str})
    df["Amount"] = df["Amount"].astype(float)
    df["Month"]  = df["Date"].str[:7]

    summary = (
        df.groupby(["Source", "Month"])
          .agg(Transactions=("Amount", "count"), Total=("Amount", "sum"))
          .reset_index()
    )
    print(f"\n  Global file: {global_file}  ({len(df)} total transactions)\n")
    return summary
