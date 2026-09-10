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
- **Self-transfer handling** — transfers between your own accounts are
  recognised and kept out of spending and income entirely. They are also
  excluded from the day-by-day calendar, because the two legs of one transfer
  often settle on different days and would otherwise look like a large
  unexplained outflow followed by an inflow.
- **Monthly analytics** — totals, per-category averages, standard deviation,
  and 95% confidence intervals, plus investment allocation by fund and
  account.
- **Anomaly detection** — flags unusually high/low spending months using the
  IQR (box-plot) method, and shows what your average looks like with those
  months excluded or redistributed.
- **Net worth over time** — cash and invested tracked month by month across
  every account, so you can see the split and how it got there.
- **Investment breakdown** — allocation by fund, by account and by asset
  class, plus how that mix has drifted over time. Asset classes are
  auto-detected and can be overridden per fund from the UI.
- **Interactive charts** — the time-series and spending charts are Vega-Lite,
  drawn in your browser: hover any month for exact figures, click a legend
  entry to isolate a series, drag to zoom.
- **Spending calendar** — a month grid showing the net movement of every day,
  with a click-through to that day's transactions.
- **Duplicate detection** — flags transactions that look like the same entry
  imported twice, and remembers the ones you dismiss.
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
│   ├── self_transfer.py     # Reciprocal cross-account transfer matching
│   ├── storage.py           # Global Excel file: append, dedupe, load
│   ├── processor.py         # Aggregates transactions into monthly stats
│   ├── investments.py       # Per-fund asset-class overrides
│   ├── duplicates.py        # Dismissed duplicate bookkeeping
│   ├── anomaly.py           # IQR-based anomalous month detection
│   ├── charts.py            # Matplotlib figures (static, used by the notebook)
│   ├── interactive.py       # Vega-Lite charts (interactive, used by the app)
│   └── style.py             # Dark theme / colour palette
├── scripts/
│   └── import_excel.py      # CLI: import an already-categorised Excel file
└── data/
    ├── categories.json               # Editable category list
    └── known_merchants.example.json  # Empty template; the real one is ignored
```

### Your data stays yours

Your transactions live in `global_spending.xlsx` at the project root, created
the first time you save a categorised import. That file — along with every
spreadsheet, CSV and PDF, your learned merchant map, your dismissed-duplicate
list and your fund classifications — is excluded by `.gitignore`. Nothing in
this repository contains real transactions, balances or holdings.

The fund registry in `finance/processor.py` is a **placeholder list of
example funds**, not anyone's portfolio. Replace those entries with the fund
names as they appear in your own exports; nothing else needs to change.

## Setup

```bash
pip install -r requirements.txt
streamlit run app.py
```

Then, in the app:
1. **Add New Month** — upload a bank export; confirm the auto-detected
   columns; categorise any unrecognised transactions; save.
2. **Expense Analysis** — monthly totals, category breakdowns and six
   interactive charts for any date range.
3. **Investments** — allocation by fund, account and asset class, and how the
   mix has changed over time.
4. **Net Worth** — cash vs invested, month by month, across your whole history.
5. **Calendar** — a month grid of daily net movement, click any day for detail.
6. **Anomalies** — which months were unusually high or low, what your average
   looks like once they're excluded, and possible duplicate transactions.
7. **Categories** — add, remove, or reset the category list.

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
