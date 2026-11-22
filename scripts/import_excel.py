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
from finance.auto_parser import parse_amount
from finance.storage import save_to_global, GLOBAL_FILE

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


def _read_file(path: str) -> pd.DataFrame:
    if str(path).lower().endswith(".csv"):
        for sep in ["\t", ",", ";"]:
            for enc in ["utf-8-sig", "latin-1"]:
                try:
                    df = pd.read_csv(str(path), sep=sep, header=None, dtype=str, encoding=enc)
                except Exception:
                    continue
                if len(df.columns) >= 4:
                    return df
        raise ValueError("Could not read the CSV file. It needs at least 4 columns: "
                         "date, amount, description, category.")
    for engine in ["openpyxl", "xlrd", None]:
        try:
            return pd.read_excel(str(path), header=None, dtype=str,
                                 **({"engine": engine} if engine else {}))
        except Exception:
            continue
    raise ValueError("Could not read the file. Make sure it's a valid .xlsx, .xls or .csv file.")


def load_existing_excel(path: str) -> pd.DataFrame:
    """The file's transactions. `.attrs["skipped"]` lists the rows that could not
    be read, so none is ever dropped without a word."""
    df = _read_file(path)

    # Detect whether there's a header row (if the first row contains words like Date/Amount)
    first = " ".join(str(v).lower() for v in df.iloc[0].tolist())
    if any(w in first for w in ("date", "fecha", "amount", "importe", "category")):
        df.columns = df.iloc[0].tolist()
        df = df.iloc[1:].reset_index(drop=True)
    else:
        # No header: assign by position (col 0=date, 1=amount, 2=description, 3=category)
        df.columns = ["Date", "Amount", "Description", "Category"] + [f"extra_{i}" for i in range(len(df.columns) - 4)]

    df = df[["Date", "Amount", "Description", "Category"]].copy()
    df = df.dropna(how="all")

    parsed = df.assign(Date=df["Date"].apply(parse_date), Amount=df["Amount"].apply(parse_amount))
    ok = parsed["Date"].notna() & parsed["Amount"].notna()
    result = parsed[ok].reset_index(drop=True)
    result["Amount"] = result["Amount"].astype(float)
    result.attrs["skipped"] = df[~ok].values.tolist()
    return result


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
    skipped = df.attrs["skipped"]
    if skipped:
        print(f"  ⚠ {len(skipped)} row(s) could not be read and will NOT be imported:")
        for row in skipped:
            print(f"      {row}")
    if df.empty:
        sys.exit(1)
    print(f"  Period: {df['Date'].min()} → {df['Date'].max()}")
    print(f"\nFirst rows:")
    print(df.head(5).to_string(index=False))

    print(f"\nImport {len(df)} rows with source='{args.source}'? (y/n): ", end="")
    if input().strip().lower() != "y":
        print("Cancelled.")
        sys.exit(0)

    n = save_to_global(df, source=args.source)
    print(f"\n✅ Done. {n} new rows added to {Path(GLOBAL_FILE).name}")


if __name__ == "__main__":
    main()
