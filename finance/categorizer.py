"""
categorizer.py — Interactive transaction categoriser with multi-bank support.

Reads a raw bank file, normalises it to the standard format
(Date, Amount, Description, Category), auto-fills known merchants, and asks
about unknowns interactively.

Supported banks
---------------
  "bbva"        — Excel (.xlsx): skiprows=4, Spanish cols, card-number stripped
  "myinvestor"  — CSV (.csv): tab-separated, Spanish header, euro-sign amounts

Adding a new bank
-----------------
  Add a new entry to BANK_PARSERS and implement a reader if the format differs.
  Everything else (categorisation, output, CLI) is shared.

Usage (command line)
--------------------
  python -m finance.categorizer may.xlsx         --bank bbva
  python -m finance.categorizer myinvestor.csv    --bank myinvestor

Usage (from notebook)
---------------------
  from finance.categorizer import categorize_csv
  categorize_csv("may.xlsx",           bank="bbva",        output_path="bbva_may2026.csv")
  categorize_csv("myinvestor_may.csv", bank="myinvestor",  output_path="myinvestor_may2026.csv")

Learned merchants are saved in data/known_merchants.json — you are never asked
about the same merchant twice across sessions.
"""

import re
import sys
import csv
import json
import argparse
import pandas as pd
from pathlib import Path
from datetime import datetime


# ─────────────────────────────────────────────────────────────────────────────
# BANK-SPECIFIC DESCRIPTION BUILDERS
# ─────────────────────────────────────────────────────────────────────────────

_CARD_NUMBER_RE = re.compile(r"^\d{16}\s*")

def _bbva_description(row: pd.Series) -> str:
    """
    For card payments: use Observaciones (strip the 16-digit card number prefix).
    For Bizum / transfers / other: combine Concepto + Movimiento.
    Examples:
        "Pago con tarjeta" + obs "4188...VOLTIO"       → "VOLTIO"
        "Bizum"            + mov "Recibido: vet"        → "Bizum: Recibido: vet"
        "Transferencia"    + mov "MYINV"               → "Transferencia: MYINV"
    """
    concepto   = str(row.get("Concepto",       "")).strip()
    movimiento = str(row.get("Movimiento",     "")).strip()
    obs        = str(row.get("Observaciones",  "")).strip()

    if movimiento.lower() in ("pago con tarjeta", "nan", ""):
        obs_clean = _CARD_NUMBER_RE.sub("", obs).strip()
        return obs_clean if obs_clean and obs_clean.lower() != "nan" else concepto
    else:
        if concepto.lower() == "nan":
            return movimiento
        return f"{concepto}: {movimiento}"


def _myinvestor_description(row: pd.Series) -> str:
    return str(row.get("Movimiento", "")).strip()


# ─────────────────────────────────────────────────────────────────────────────
# BANK PARSER REGISTRY
# ─────────────────────────────────────────────────────────────────────────────
# Keys:
#   file_type   — "excel" or "csv"
#   skip_rows   — rows to skip before the header row
#   sep         — CSV separator (csv only)
#   encoding    — file encoding (csv only)
#   col_date    — column name for the transaction date
#   col_amount  — column name for the amount
#   date_format — strptime format string
#   parse_desc  — callable(row) → str  (builds the description field)

BANK_PARSERS: dict = {

    # ── BBVA ──────────────────────────────────────────────────────────────────
    # Export: Excel (.xlsx) — "Informe BBVA" sheet
    # Structure:
    #   row 0 → "Últimos movimientos"  (title)
    #   row 1 → "Fecha de generación del informe: ..."
    #   row 2 → empty
    #   row 3 → empty
    #   row 4 → column headers  ← skiprows=4 lands here
    #   row 5+ → data
    "bbva": {
        "label":        "BBVA",
        "file_type":    "excel",
        "skip_rows":    4,
        "col_date":     "Fecha",
        "col_amount":   "Importe",
        "date_format":  "%d/%m/%Y",
        "parse_desc":   _bbva_description,
    },

    # ── MyInvestor ────────────────────────────────────────────────────────────
    # Export: CSV (.csv) — tab-separated
    # Structure:
    #   row 0 → "Movimientos"  (title)
    #   row 1 → empty
    #   row 2 → column headers  ← skiprows=2 lands here
    #   row 3+ → data
    # Amount format: "-299.99€"  or  "1,000.00€"
    "myinvestor": {
        "label":        "MyInvestor",
        "file_type":    "csv",
        "skip_rows":    2,
        "sep":          "\t",
        "encoding":     "utf-8-sig",
        "col_date":     "Fecha Operaci\u00f3n",   # "Fecha Operación"
        "col_amount":   "Importe",
        "date_format":  "%d/%m/%Y",
        "parse_desc":   _myinvestor_description,
    },
}


# ─────────────────────────────────────────────────────────────────────────────
# CATEGORIES
# ─────────────────────────────────────────────────────────────────────────────

CATEGORIES = [
    "Groceries",
    "Dining & Food",
    "Transport",
    "Gas",
    "Health & Beauty",
    "Gym",
    "Entertainment",
    "Shopping",
    "Education",
    "Rent",
    "Electricity",
    "Water",
    "Phone",
    "Social & Gifts",
    "Fees & Tax",
    "Investments",
    "Income",
    "Interest",
    "Self-transfer",
    "Personal Care",
    "Pet",
    "Other",
]


# ─────────────────────────────────────────────────────────────────────────────
# BUILT-IN MERCHANT MAP  (keyword → category, all lowercase)
# ─────────────────────────────────────────────────────────────────────────────

BASE_MERCHANTS: dict[str, str] = {
    # Groceries
    "lidl":                  "Groceries",
    "mercadona":             "Groceries",
    "carrefour":             "Groceries",
    "alcampo":               "Groceries",
    "aldi":                  "Groceries",
    "consum":                "Groceries",
    "eroski":                "Groceries",
    "hipercor":              "Groceries",
    "supermercado dia":      "Groceries",     # avoid matching "media" — user can add 'dia' keyword manually
    " dia ":                 "Groceries",     # catches "DIA 9434" etc. with surrounding spaces
    "simply":                "Groceries",
    "fruteria":              "Groceries",
    "obrador":               "Groceries",
    "primaprix":             "Groceries",
    # Dining & Food
    "mcdonald":              "Dining & Food",
    "burger king":           "Dining & Food",
    "telepizza":             "Dining & Food",
    "dominos":               "Dining & Food",
    "just eat":              "Dining & Food",
    "glovo":                 "Dining & Food",
    "uber eats":             "Dining & Food",
    "deliveroo":             "Dining & Food",
    "pizza":                 "Dining & Food",
    "crispy chicken":        "Dining & Food",
    # Transport
    "renfe":                 "Transport",
    "cabify":                "Transport",
    "blablacar":             "Transport",
    "ouigo":                 "Transport",
    "iryo":                  "Transport",
    "alsa":                  "Transport",
    "voltio":                "Transport",
    "uber":                  "Transport",
    # Fuel
    "repsol":                "Gas",
    "cepsa":                 "Gas",
    "galp":                  "Gas",
    # Health
    "farmacia":              "Health & Beauty",
    "clinica":               "Health & Beauty",
    "sanitas":               "Health & Beauty",
    "adeslas":               "Health & Beauty",
    "dentista":              "Health & Beauty",
    "optica":                "Health & Beauty",
    #Pet
    "veterinario":           "Pet",
    "centro veterinario":    "Pet",
    # Gym
    "gym":                   "Gym",
    # Entertainment / Subscriptions
    "spotify":               "Entertainment",
    "netflix":               "Entertainment",
    "hbo":                   "Entertainment",
    "disney":                "Entertainment",
    "youtube":               "Entertainment",
    "steam":                 "Entertainment",
    "cines":                 "Entertainment",
    "kinepolis":             "Entertainment",
    "claude.ai":             "Entertainment",   # AI subscription
    "media markt protect":   "Entertainment",   # device insurance / tech
    # Shopping
    "amazon":                "Shopping",
    "ikea":                  "Shopping",
    "leroy merlin":          "Shopping",
    "zara":                  "Shopping",
    "mango":                 "Shopping",
    "primark":               "Shopping",
    "fnac":                  "Shopping",
    "flying tiger":          "Shopping",
    "game ":                 "Shopping",         # videogame shop
    "casa del libro":        "Shopping",
    # Education
    "universidad":           "Education",
    # Utilities
    "endesa":                "Electricity",
    "iberdrola":             "Electricity",
    "naturgy":               "Gas",
    "vodafone":              "Phone",
    "movistar":              "Phone",
    "orange":                "Phone",
    "agua":                  "Water",
    # Social / Gifts
    "floristeria":           "Social & Gifts",
    "cumple":                "Social & Gifts",
    "regalo":                "Social & Gifts",
    # Income
    "nomina":                "Income",
    "salary":                "Income",
    "recibido":              "Income",           # Bizum received (overridden by keyword below)
    # Interest
    "periodo":               "Interest",         # MyInvestor interest periods
    "interest income":       "Interest",
    # Self-transfers (between your own accounts)
    "myinv":                 "Self-transfer",
    "myinvestor":            "Self-transfer",
    # NOTE: "transfer received" was removed on purpose — it is too generic and
    # matched real incoming income/salary deposits, not just transfers between
    # your own accounts. Incoming transfers now require manual categorisation
    # (or a specific learned keyword) instead of being auto-assumed to be a
    # self-transfer.
    # Investments.
    # These are generic fund-name fragments, deliberately not anyone's real
    # holdings — same reasoning as the placeholder registry in processor.py.
    # Add the fund families that appear in your own exports here (a lowercase
    # substring of the description is enough) so their rows land under
    # Investments automatically.
    "index fund":            "Investments",
    "index etf":             "Investments",
    "etf":                   "Investments",
    "acc":                   "Investments",
    "dis eur":               "Investments",
}

KNOWN_MERCHANTS_FILE = Path(__file__).resolve().parent.parent / "data" / "known_merchants.json"


# ─────────────────────────────────────────────────────────────────────────────
# SIGN-CONSISTENCY CHECK
# ─────────────────────────────────────────────────────────────────────────────
# Sign alone can't identify a self-transfer (the outgoing leg is negative
# like any purchase; the incoming leg is positive like any income). But most
# other categories only ever make sense with one sign, so a mismatch is a
# strong signal of a mis-click during review — flag it, don't decide it.

EXPECTED_SIGN: dict[str, str | None] = {
    "Groceries":        "-",
    "Dining & Food":    "-",
    "Transport":        "-",
    "Gas":              "-",
    "Health & Beauty":  "-",
    "Gym":              "-",
    "Entertainment":    "-",
    "Shopping":         "-",
    "Education":        "-",
    "Rent":             "-",
    "Electricity":      "-",
    "Water":            "-",
    "Phone":            "-",
    "Social & Gifts":   "-",
    "Fees & Tax":       "-",
    "Investments":      "-",
    "Income":           "+",
    "Interest":         "+",
    "Self-transfer":    None,   # either sign is valid
    "Personal Care":    "-",
    "Pet":              "-",
    "Other":            None,   # ambiguous, skip
}


def sign_mismatch(category: str, amount: float) -> bool:
    """True if `amount`'s sign contradicts what's expected for `category`."""
    expected = EXPECTED_SIGN.get(category)
    if expected is None:
        return False
    return (expected == "+") != (amount >= 0)


# ─────────────────────────────────────────────────────────────────────────────
# CORE HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def load_known_merchants() -> dict:
    if KNOWN_MERCHANTS_FILE.exists():
        with open(KNOWN_MERCHANTS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_known_merchants(known: dict) -> None:
    with open(KNOWN_MERCHANTS_FILE, "w", encoding="utf-8") as f:
        json.dump(known, f, ensure_ascii=False, indent=2)


def find_category(description: str, known: dict) -> str | None:
    """
    Return category for description.
    User-learned merchants are checked first, then built-in map.
    Within each map, longer keywords win over shorter ones to avoid false
    partial matches (e.g. 'media markt' wins over 'dia ' inside 'media').
    """
    desc_lower = description.lower()
    for kw, cat in sorted(known.items(), key=lambda x: -len(x[0])):
        if kw in desc_lower:
            return cat
    for kw, cat in sorted(BASE_MERCHANTS.items(), key=lambda x: -len(x[0])):
        if kw in desc_lower:
            return cat
    return None


def ask_user_category(description: str, amount: float) -> tuple[str, str | None]:
    """Interactively ask for a category. Returns (category, keyword_or_None)."""
    sign = "+" if amount >= 0 else ""
    print(f"\n{'─' * 64}")
    print(f"  UNKNOWN TRANSACTION")
    print(f"  Description : {description}")
    print(f"  Amount      : {sign}{amount:,.2f} €")
    print(f"{'─' * 64}")
    for i, cat in enumerate(CATEGORIES, 1):
        print(f"    {i:2}. {cat}")
    print(f"     0. Skip (mark as 'Other')")
    print()

    while True:
        resp = input("  Your choice (number): ").strip()
        if resp == "0":
            return "Other", None
        if resp.isdigit() and 1 <= int(resp) <= len(CATEGORIES):
            break
        print("  Please enter a valid number.")

    category = CATEGORIES[int(resp) - 1]
    print(f"\n  Category: {category}")
    keyword = input(
        f"  Description: '{description}'\n"
        f"  Keyword to remember this merchant (Enter to skip): "
    ).strip().lower()

    return category, keyword or None


# ─────────────────────────────────────────────────────────────────────────────
# BANK-SPECIFIC READERS
# ─────────────────────────────────────────────────────────────────────────────

def _parse_amount(raw) -> float | None:
    """Convert a raw amount value (str, int, float) to float. Returns None on failure."""
    if raw is None:
        return None
    s = str(raw).strip()
    if not s or s.lower() == "nan":
        return None
    # MyInvestor format: "1,000.00€" or "-299.99€"
    s = s.replace("\u20ac", "").replace("\u00a0", "")
    # If thousands separator is comma and decimal is period: "1,000.00" → remove commas
    # If Spanish format "1.000,00" → swap
    if re.search(r"\d,\d{3}", s) and "." in s:
        s = s.replace(",", "")       # English thousands: "1,000.00" → "1000.00"
    elif re.search(r"\d\.\d{3}", s) and "," in s:
        s = s.replace(".", "").replace(",", ".")   # Spanish: "1.000,00" → "1000.00"
    else:
        s = s.replace(",", ".")      # fallback: treat comma as decimal
    try:
        return float(s)
    except ValueError:
        return None


def _read_bank_file(path: Path, bank: str) -> list[dict]:
    """
    Read a raw bank file and return normalised rows:
        [{"date": "YYYY-MM-DD", "amount": float, "description": str}, ...]
    """
    cfg = BANK_PARSERS[bank]

    if cfg["file_type"] == "excel":
        df = pd.read_excel(path, skiprows=cfg["skip_rows"], dtype=str)
    else:
        # CSV — try multiple encodings
        df = None
        for enc in [cfg.get("encoding", "utf-8-sig"), "latin-1", "utf-8"]:
            try:
                df = pd.read_csv(
                    path,
                    sep=cfg.get("sep", ","),
                    skiprows=cfg["skip_rows"],
                    encoding=enc,
                    dtype=str,
                    engine="python",
                )
                break
            except Exception:
                continue
        if df is None:
            raise IOError(
                f"Could not read {path}.\n"
                f"If it's an Excel file, rename it to .xlsx and use the correct bank."
            )

    # Normalise columns and drop empty rows
    df.columns = [str(c).strip() for c in df.columns]
    df = df.map(lambda x: x.strip() if isinstance(x, str) else x)
    df = df.dropna(how="all")

    rows = []
    for _, row in df.iterrows():
        # Date
        raw_date = str(row.get(cfg["col_date"], "")).strip()
        if not raw_date or raw_date.lower() == "nan":
            continue
        try:
            dt = datetime.strptime(raw_date, cfg["date_format"])
            date_str = dt.strftime("%Y-%m-%d")
        except ValueError:
            continue

        # Amount
        amount = _parse_amount(row.get(cfg["col_amount"]))
        if amount is None:
            continue

        # Description
        description = cfg["parse_desc"](row).strip()
        if not description or description.lower() == "nan":
            continue

        rows.append({"date": date_str, "amount": amount, "description": description})

    return rows


# ─────────────────────────────────────────────────────────────────────────────
# MAIN FUNCTION
# ─────────────────────────────────────────────────────────────────────────────

def categorize_csv(
    input_path:  str,
    bank:        str,
    output_path: str | None = None,
) -> str:
    """
    Read a raw bank file, auto-categorise known transactions, ask about unknowns.

    Parameters
    ----------
    input_path  : Path to the raw file from your bank (.xlsx or .csv).
    bank        : Bank key — "bbva" or "myinvestor".
    output_path : Where to save the categorised CSV.
                  Defaults to <input_stem>_categorized.csv.

    Returns
    -------
    Path of the generated output CSV (str).
    """
    if bank not in BANK_PARSERS:
        available = ", ".join(f'"{k}"' for k in BANK_PARSERS)
        raise ValueError(f"Unknown bank '{bank}'. Available: {available}")

    input_path  = Path(input_path)
    output_path = Path(output_path) if output_path else (
        input_path.parent / (input_path.stem + "_categorized.csv")
    )

    cfg   = BANK_PARSERS[bank]
    known = load_known_merchants()

    print(f"\n{'='*64}")
    print(f"  TRANSACTION CATEGORISER  ·  {cfg['label']}")
    print(f"{'='*64}")
    print(f"  Input  : {input_path}")
    print(f"  Output : {output_path}")
    print(f"  Known merchants: {len(known) + len(BASE_MERCHANTS)}")
    print(f"{'='*64}\n")

    raw_rows = _read_bank_file(input_path, bank)
    print(f"  {len(raw_rows)} transactions read\n")

    output_rows   = []
    auto_count    = 0
    asked_count   = 0
    newly_learned = 0
    total         = len(raw_rows)

    for i, row in enumerate(raw_rows, 1):
        description = row["description"]
        amount      = row["amount"]
        date_str    = row["date"]

        category = find_category(description, known)

        if category:
            auto_count += 1
            print(f"  [{i:>4}/{total}] ok  {category:<22}  {description[:36]}")
        else:
            asked_count += 1
            category, keyword = ask_user_category(description, amount)
            if keyword:
                known[keyword] = category
                newly_learned += 1
                print(f"  → Saved: '{keyword}' = {category}")

        output_rows.append({
            "Date":        date_str,
            "Amount":      amount,
            "Description": description,
            "Category":    category,
        })

    if newly_learned > 0:
        save_known_merchants(known)
        print(f"\n  💾 {newly_learned} new merchant(s) saved to {KNOWN_MERCHANTS_FILE.name}")

    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["Date", "Amount", "Description", "Category"])
        writer.writeheader()
        writer.writerows(output_rows)

    print(f"\n{'='*64}")
    print(f"  Total: {total}  |  Auto: {auto_count}  |  "
          f"Asked: {asked_count}  |  Saved: {newly_learned}")
    print(f"  Output: {output_path}")
    print(f"{'='*64}\n")

    return str(output_path)


def list_banks() -> None:
    print("\nSupported banks:")
    for key, cfg in BANK_PARSERS.items():
        print(f"  {key:<15}  {cfg['label']}  ({cfg['file_type']})")
    print()


# ─────────────────────────────────────────────────────────────────────────────
# QUICK TEST  (run: python -m finance.categorizer --test may.xlsx --bank bbva)
# ─────────────────────────────────────────────────────────────────────────────

def _dry_run(input_path: str, bank: str) -> None:
    """Print parsed rows without asking for categories — useful to verify the parser."""
    rows = _read_bank_file(Path(input_path), bank)
    known = load_known_merchants()
    print(f"\n  {len(rows)} rows parsed from {input_path!r}\n")
    print(f"  {'Date':<12}  {'Amount':>9}  {'Auto-cat':<22}  Description")
    print(f"  {'─'*12}  {'─'*9}  {'─'*22}  {'─'*40}")
    for r in rows:
        cat = find_category(r["description"], known) or "?"
        print(f"  {r['date']:<12}  {r['amount']:>9.2f}  {cat:<22}  {r['description'][:50]}")


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Categorise a raw bank file into the standard analysis format."
    )
    parser.add_argument("input",           nargs="?",           help="Bank file (.xlsx or .csv)")
    parser.add_argument("--bank",  "-b",                        help='"bbva" or "myinvestor"')
    parser.add_argument("--output", "-o",                       help="Output CSV path (optional)")
    parser.add_argument("--test",  "-t",   action="store_true", help="Dry-run: show parsed rows, no interaction")
    parser.add_argument("--list-banks",    action="store_true", help="Show supported banks and exit")
    args = parser.parse_args()

    if args.list_banks:
        list_banks()
        sys.exit(0)

    if not args.input or not args.bank:
        parser.print_help()
        print("\nExamples:")
        print("  python -m finance.categorizer may.xlsx          --bank bbva")
        print("  python -m finance.categorizer myinvestor_may.csv --bank myinvestor")
        print("  python -m finance.categorizer may.xlsx          --bank bbva --test\n")
        sys.exit(1)

    if not Path(args.input).exists():
        print(f"\nERROR: File not found: '{args.input}'\n")
        sys.exit(1)

    if args.test:
        _dry_run(args.input, args.bank)
    else:
        categorize_csv(args.input, bank=args.bank, output_path=args.output)
