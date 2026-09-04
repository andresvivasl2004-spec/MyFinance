"""
import_excel.py — Import an already-categorised Excel file into the global file.

Usage:
    python scripts/import_excel.py my_excel.xlsx
    python scripts/import_excel.py my_excel.xlsx --source "MyInvestor"
"""

import sys
import argparse
import pandas as pd
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from finance.storage import save_to_global

# Date formats tried automatically
DATE_FORMATS = [
    "%d-%m-%y",    # 28-04-26
    "%d-%m-%Y",    # 28-04-2026
    "%d/%m/%Y",    # 28/04/2026
    "%d/%m/%y",    # 28/04/26
    "%Y-%m-%d",    # 2026-04-28
]

def parse_date(raw) -> str | None:
    s = str(raw).strip().split(" ")[0]  # drop the time part, if any
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def load_existing_excel(path: str) -> pd.DataFrame:
    # Try different engines until one works
    df = None
    for engine in ["openpyxl", "xlrd", None]:
        try:
            kwargs = {"header": None, "dtype": str}
            if engine:
                kwargs["engine"] = engine
            df = pd.read_excel(str(path), **kwargs)
            break
        except Exception:
            continue
    if df is None:
        raise ValueError(
            "Could not read the file. Make sure it's a valid .xlsx or .xls file.\n"
            "If it's a .csv, rename it to .csv and import it like this:\n"
            "  python scripts/import_excel.py file.csv"
        )

    # ── CSV support ──────────────────────────────────────────────────────────
    if str(path).lower().endswith(".csv"):
        df = None
        for sep in ["	", ",", ";"]:
            for enc in ["utf-8-sig", "latin-1", "utf-8"]:
                try:
                    df = pd.read_csv(str(path), sep=sep, header=None, dtype=str, encoding=enc)
                    if len(df.columns) >= 4:
                        break
                except Exception:
                    continue
            if df is not None and len(df.columns) >= 4:
                break
        if df is None:
            raise ValueError("Could not read the CSV file.")

    # Detect whether there's a header row (if the first row contains words like Date/Amount)
    first = " ".join(str(v).lower() for v in df.iloc[0].tolist())
    if any(w in first for w in ("date", "fecha", "amount", "importe", "category")):
        df.columns = df.iloc[0].tolist()
        df = df.iloc[1:].reset_index(drop=True)
    else:
        # No header: assign by position (col 0=date, 1=amount, 2=description, 3=category)
        df.columns = ["Date", "Amount", "Description", "Category"] + [f"extra_{i}" for i in range(len(df.columns) - 4)]

    df = df[["Date", "Amount", "Description", "Category"]].copy()
    df = df.dropna(subset=["Date", "Amount"])

    # Normalise dates
    df["Date"] = df["Date"].apply(parse_date)
    df = df[df["Date"].notna()].copy()

    # Normalise amounts
    df["Amount"] = (
        df["Amount"].astype(str)
        .str.replace("€", "", regex=False)
        .str.replace(",", ".", regex=False)
        .str.strip()
    )
    df["Amount"] = pd.to_numeric(df["Amount"], errors="coerce")
    df = df[df["Amount"].notna()].copy()

    return df.reset_index(drop=True)


def main():
    parser = argparse.ArgumentParser(description="Import a categorised Excel file into the global file.")
    parser.add_argument("file",            help="Excel file to import (.xlsx)")
    parser.add_argument("--source", "-s",  default="Imported", help='Account label (e.g. "MyInvestor")')
    args = parser.parse_args()

    path = Path(args.file)
    if not path.exists():
        print(f"\nERROR: '{path}' not found\n")
        sys.exit(1)

    print(f"\nReading {path}...")
    df = load_existing_excel(str(path))

    print(f"\n  {len(df)} rows found")
    print(f"  Period: {df['Date'].min()} → {df['Date'].max()}")
    print(f"\nFirst rows:")
    print(df.head(5).to_string(index=False))

    print(f"\nImport {len(df)} rows with source='{args.source}'? (y/n): ", end="")
    if input().strip().lower() != "y":
        print("Cancelled.")
        sys.exit(0)

    n = save_to_global(df, source=args.source)
    print(f"\n✅ Done. {n} new rows added to global_spending.xlsx")


if __name__ == "__main__":
    main()
