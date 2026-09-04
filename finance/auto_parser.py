"""
auto_parser.py — Detect and parse any bank export without specifying the bank.

Tries different header-row positions (skiprows 0-6) and scores each column
for how likely it is to be a date, amount, or description. Picks the best
combination automatically.

Usage
-----
    from finance.auto_parser import detect_and_parse

    result = detect_and_parse("may.xlsx")
    print(result["rows"])       # list of {date, amount, description}
    print(result["detected"])   # which columns were chosen
    print(result["confidence"]) # 0.0 – 1.0
"""

import re
import pandas as pd
from pathlib import Path
from datetime import datetime


# ── Date format candidates ─────────────────────────────────────────────────────
DATE_FORMATS = [
    "%Y-%m-%d",   # 2026-04-28  (standard — try first)
    "%d-%m-%y",   # 28-04-26
    "%d-%m-%Y",   # 28-04-2026
    "%d/%m/%Y",   # 28/04/2026
    "%d/%m/%y",   # 28/04/26
    "%m/%d/%Y",   # 04/28/2026
    "%d.%m.%Y",   # 28.04.2026
]

# ── Column-role keyword hints ──────────────────────────────────────────────────
DATE_KW   = {"date", "fecha", "f.valor", "f.operacion", "valor", "operac", "booking"}
AMOUNT_KW = {"amount", "importe", "monto", "cantidad", "cargo", "betrag", "montant"}
DESC_KW   = {"description", "concepto", "movimiento", "detalle", "observ",
             "detail", "reason", "narrative", "reference", "text", "memo"}
SKIP_KW   = {"saldo", "balance", "disponible", "currency", "divisa",
             "unnamed", "moneda", "cuenta", "account"}


# ── Low-level parsers ──────────────────────────────────────────────────────────

def _try_date(s: str) -> bool:
    s = str(s).strip().split(" ")[0]
    for fmt in DATE_FORMATS:
        try:
            datetime.strptime(s, fmt)
            return True
        except ValueError:
            continue
    return False


def parse_date(s) -> str | None:
    s = str(s).strip().split(" ")[0]
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def parse_amount(s) -> float | None:
    if pd.isna(s):
        return None
    s = str(s).strip().replace("€", "").replace(" ", "").replace("\xa0", "")
    if not s or s.lower() == "nan":
        return None
    # Spanish thousands: 1.234,56 → 1234.56
    if re.search(r"\d\.\d{3}[,]", s):
        s = s.replace(".", "").replace(",", ".")
    elif re.search(r"\d,\d{3}\.", s):
        # English thousands: 1,234.56 → 1234.56
        s = s.replace(",", "")
    else:
        s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


# ── Column scoring ─────────────────────────────────────────────────────────────

def _score_date(col: str, series: pd.Series) -> float:
    score = 0.0
    col_l = col.lower()
    if any(kw in col_l for kw in DATE_KW):
        score += 4
    if any(kw in col_l for kw in SKIP_KW):
        score -= 4
    sample = series.dropna().head(25).astype(str)
    if len(sample) == 0:
        return score
    hits = sum(_try_date(v) for v in sample)
    score += (hits / len(sample)) * 6
    return score


def _score_amount(col: str, series: pd.Series) -> float:
    score = 0.0
    col_l = col.lower()
    if any(kw in col_l for kw in AMOUNT_KW):
        score += 4
    if any(kw in col_l for kw in SKIP_KW):
        score -= 5   # "Saldo" / "Balance" is not the transaction amount
    sample = series.dropna().head(25)
    if len(sample) == 0:
        return score
    parsed = [parse_amount(v) for v in sample]
    valid  = [v for v in parsed if v is not None]
    if not valid:
        return score
    score += (len(valid) / len(sample)) * 4
    if any(v < 0 for v in valid):   # has negatives → likely transaction deltas
        score += 3
    if all(v > 0 for v in valid) and "saldo" in col_l:
        score -= 3
    return score


def _score_desc(col: str, series: pd.Series) -> float:
    score = 0.0
    col_l = col.lower()
    if any(kw in col_l for kw in DESC_KW):
        score += 4
    if any(kw in col_l for kw in DATE_KW | AMOUNT_KW | SKIP_KW):
        score -= 3
    sample = series.dropna().head(25)
    if len(sample) == 0:
        return score
    as_str = sample.astype(str)
    avg_len = as_str.str.len().mean()
    if avg_len > 4:
        score += 2
    if as_str.nunique() / len(as_str) > 0.3:   # high cardinality → text
        score += 2
    return score


# ── File readers ───────────────────────────────────────────────────────────────

def _read_csv(path: str, skiprows: int) -> pd.DataFrame | None:
    for sep in [",", "\t", ";"]:
        for enc in ["utf-8-sig", "utf-8", "latin-1"]:
            try:
                df = pd.read_csv(path, sep=sep, skiprows=skiprows,
                                 dtype=str, encoding=enc)
                if len(df.columns) >= 3 and len(df) >= 2:
                    return df
            except Exception:
                continue
    return None


def _read_excel(path: str, skiprows: int) -> pd.DataFrame | None:
    for engine in ["openpyxl", "xlrd", None]:
        try:
            kw = {"skiprows": skiprows, "dtype": str}
            if engine:
                kw["engine"] = engine
            df = pd.read_excel(path, **kw)
            if len(df.columns) >= 3 and len(df) >= 2:
                return df
        except Exception:
            continue
    return None


# ── Main detection function ────────────────────────────────────────────────────

def detect_and_parse(path: str) -> dict:
    """
    Auto-detect the structure of any bank export and return normalised rows.

    Parameters
    ----------
    path : Path to a .csv, .xlsx, or .xls file.

    Returns
    -------
    dict with keys:
        "rows"       : list of {"date": "YYYY-MM-DD", "amount": float, "description": str}
        "detected"   : {"date_col", "amount_col", "desc_col", "desc2_col", "skip_rows"}
        "all_columns": list of all column names in the parsed DataFrame
        "raw_df"     : the DataFrame as read (for column-override in the UI)
        "confidence" : float 0.0–1.0
        "n_ok"       : number of successfully parsed rows
    """
    ext = Path(path).suffix.lower()
    best = None
    best_score = -999.0

    for skiprows in range(7):
        if ext == ".csv":
            df = _read_csv(path, skiprows)
        elif ext in (".xlsx", ".xls"):
            df = _read_excel(path, skiprows)
        else:
            df = _read_csv(path, skiprows) or _read_excel(path, skiprows)

        if df is None or len(df) < 2:
            continue

        df.columns = [str(c).strip() for c in df.columns]
        df = df.dropna(how="all").reset_index(drop=True)
        if len(df) < 2:
            continue

        scores = {col: {
            "date":   _score_date(col, df[col]),
            "amount": _score_amount(col, df[col]),
            "desc":   _score_desc(col, df[col]),
        } for col in df.columns}

        date_col   = max(df.columns, key=lambda c: scores[c]["date"])
        amount_col = max((c for c in df.columns if c != date_col),
                         key=lambda c: scores[c]["amount"])
        desc_col   = max((c for c in df.columns if c not in (date_col, amount_col)),
                         key=lambda c: scores[c]["desc"])

        # Optional second description column (e.g. BBVA Concepto + Movimiento)
        desc2_candidates = [
            c for c in df.columns
            if c not in (date_col, amount_col, desc_col)
            and scores[c]["desc"] >= 2.5
        ]
        desc2_col = desc2_candidates[0] if desc2_candidates else None

        total = (scores[date_col]["date"] +
                 scores[amount_col]["amount"] +
                 scores[desc_col]["desc"])

        if total > best_score:
            best_score = total
            best = {
                "df": df, "skip_rows": skiprows,
                "date_col": date_col, "amount_col": amount_col,
                "desc_col": desc_col, "desc2_col": desc2_col,
                "scores": scores, "total_score": total,
            }

    if best is None:
        raise ValueError(
            "Could not detect the file structure.\n"
            "Make sure it has at least 3 columns and 2 rows of data."
        )

    return _build_result(best)


def reparse(raw_df: pd.DataFrame, date_col: str, amount_col: str,
            desc_col: str, desc2_col: str | None = None) -> list[dict]:
    """
    Re-parse a raw DataFrame with manually selected columns.
    Used when the user overrides the auto-detected column mapping in the UI.
    """
    rows = []
    for _, row in raw_df.iterrows():
        date   = parse_date(row.get(date_col, ""))
        amount = parse_amount(row.get(amount_col))
        if not date or amount is None:
            continue

        desc = str(row.get(desc_col, "")).strip()
        if desc2_col:
            d2 = str(row.get(desc2_col, "")).strip()
            if d2 and d2.lower() not in ("nan", "", desc.lower()):
                desc = f"{desc}: {d2}" if desc else d2

        if not desc or desc.lower() == "nan":
            continue
        rows.append({"date": date, "amount": amount, "description": desc})
    return rows


def _build_result(best: dict) -> dict:
    df        = best["df"]
    date_col  = best["date_col"]
    amt_col   = best["amount_col"]
    desc_col  = best["desc_col"]
    desc2_col = best["desc2_col"]

    rows = reparse(df, date_col, amt_col, desc_col, desc2_col)
    confidence = min(1.0, best["total_score"] / 15.0)

    return {
        "rows":       rows,
        "detected":   {
            "date_col":   date_col,
            "amount_col": amt_col,
            "desc_col":   desc_col,
            "desc2_col":  desc2_col,
            "skip_rows":  best["skip_rows"],
        },
        "all_columns": list(df.columns),
        "raw_df":      df,
        "confidence":  confidence,
        "n_ok":        len(rows),
    }
