# MyFinance

A personal finance dashboard that turns raw bank exports into categorised
transactions, spending analytics, and anomaly detection — no manual
spreadsheet wrangling required.

Upload a bank statement (any format — the columns are auto-detected), the
app learns how to categorise your merchants over time, and you get a
dashboard with monthly spending breakdowns, investment allocation, and
outlier-month detection.

## Features

- **Auto-detection of any bank export** (`.xlsx`, `.xls`, `.csv`) — no need
  to tell it which bank the file came from; column roles (date, amount,
  description) are scored and picked automatically.
- **Learning categoriser** — known merchants are categorised instantly;
  unknown ones are asked about once and remembered from then on.
- **Self-transfer detection** — matches reciprocal transactions between your
  own accounts (same amount, opposite sign, close date) so internal
  transfers never get miscounted as spending or income.
- **Monthly analytics** — totals, per-category averages, standard deviation,
  and 95% confidence intervals, plus investment allocation by fund and
  account.
- **Anomaly detection** — flags unusually high/low spending months using the
  IQR (box-plot) method, and shows what your average looks like with those
  months excluded or redistributed.
- **Editable category list** — add, remove, or reset spending categories
  from the UI.

## Project structure

```
.
├── app.py                   # Streamlit dashboard (entry point)
├── analysis.ipynb           # Notebook workflow (categorise → review → analyse)
├── finance/                 # Core library
│   ├── auto_parser.py       # Detects file structure of any bank export
│   ├── categorizer.py       # Interactive/auto transaction categoriser
│   ├── categories.py        # Persistent category list (data/categories.json)
│   ├── self_transfer.py     # Reciprocal cross-account transfer detection
│   ├── storage.py           # Global Excel file: append, dedupe, load
│   ├── processor.py         # Aggregates transactions into monthly stats
│   ├── anomaly.py           # IQR-based anomalous month detection
│   ├── charts.py            # Matplotlib figures
│   └── style.py             # Dark theme / colour palette
├── scripts/
│   └── import_excel.py      # CLI: import an already-categorised Excel file
└── data/
    ├── categories.json      # Editable category list
    └── known_merchants.json # Learned merchant → category mappings
```

Your actual transaction data lives in `global_spending.xlsx` at the project
root, generated the first time you save a categorised import. It's excluded
from version control via `.gitignore` — nobody but you sees your spending.

## Setup

```bash
pip install -r requirements.txt
streamlit run app.py
```

Then, in the app:
1. **Add New Month** — upload a bank export; confirm the auto-detected
   columns; categorise any unrecognised transactions; save.
2. **Expense Analysis** — view monthly totals, category breakdowns, and
   charts for any date range.
3. **Anomalies** — see which months were unusually high/low spending, and
   what your "normal" average looks like once they're excluded.
4. **Categories** — add, remove, or reset the category list.

## Notebook workflow (alternative to the app)

`analysis.ipynb` walks through the same pipeline from a notebook:
categorise a raw file → review the table → append to the global Excel →
run the full statistical/chart analysis, including the anomaly detector.

## Supported banks

Built-in parsers exist for:
- **BBVA** — Excel export (Spanish column headers)
- **MyInvestor** — CSV export (Spanish column headers, tab-separated)

Both are Spanish banks, so their raw export files use Spanish column names
and merchant text — that's the banks' data format, not the app's language,
so those specific strings are intentionally left as-is in `categorizer.py`.

Any other bank's export can still be used via the Streamlit app's **Add New
Month** tab, since `auto_parser.py` detects column roles heuristically
rather than requiring bank-specific configuration.

## CLI tools

```bash
# Categorise a raw bank file interactively from the terminal
python -m finance.categorizer may.xlsx --bank bbva

# Import an already-categorised Excel file into the global file
python scripts/import_excel.py my_file.xlsx --source "MyInvestor"
```
