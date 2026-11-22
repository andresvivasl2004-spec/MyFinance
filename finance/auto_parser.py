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

# ── Spanish month abbreviations, as used in Trade Republic-style PDF
#    statements ("18 mar 2025", "08 sept 2026") ──────────────────────────────
_MESES_ES = {
    "ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6,
    "jul": 7, "ago": 8, "sept": 9, "sep": 9, "oct": 10, "nov": 11, "dic": 12,
}


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


# ── PDF statements (Trade Republic-style) ──────────────────────────────────────
#
# Unlike CSV/Excel, a PDF has no real columns — just text positioned on a
# page. This reader is built for Trade Republic's account-statement layout
# (FECHA | TIPO | DESCRIPCIÓN | ENTRADA DE DINERO | SALIDA DE DINERO |
# BALANCE, repeating across pages): it buckets every word into a column by
# its horizontal position, groups words into transaction rows by finding
# each date (which always starts a new row), and stops before the closing
# "RESUMEN DEL BALANCE" / legal-notes pages. It was verified against a real
# 24-page / 376-transaction statement: total money in, total money out, and
# the running balance on every single row matched the PDF exactly.
#
# A bank statement laid out differently will need its own reader — this one
# is not a generic PDF-table parser.

_PDF_FOOTER_TOP = 755.0            # Trade Republic repeats its footer here on every page
_PDF_STOP_WORDS = {"RESUMEN", "NOTAS", "CONFIRMACIÓN"}   # closing sections to cut off
_PDF_DAY_RE     = re.compile(r"^\d{1,2}$")
_PDF_DATE_RE    = re.compile(r"(\d{1,2})\s+([a-zé]+)\s+(\d{4})", re.I)


def _pdf_col(x0: float) -> str:
    """Which column a word belongs to, by its left edge (x0) on the page."""
    if x0 < 100:  return "date"
    if x0 < 155:  return "type"
    if x0 < 405:  return "desc"
    if x0 < 427:  return "in"        # ENTRADA DE DINERO
    if x0 < 465:  return "out"       # SALIDA DE DINERO
    return "balance"


def _pdf_amount(s: str) -> float | None:
    s = (s or "").replace("€", "").replace(".", "").replace(",", ".").strip()
    return float(s) if s else None


def _pdf_page_cutoff(words: list) -> float:
    """Where the transaction table ends on this page. The closing summary
    and legal-notes sections use the same title style as the *opening*
    summary table on page 1, so a stop word only counts if it appears after
    the first transaction date already seen on this page."""
    first_marker = next(
        (w["top"] for w in sorted(words, key=lambda w: w["top"])
         if _pdf_col(w["x0"]) == "date" and _PDF_DAY_RE.match(w["text"])),
        None,
    )
    cutoff = _PDF_FOOTER_TOP
    if first_marker is not None:
        for w in words:
            if w["text"].upper() in _PDF_STOP_WORDS and w["top"] > first_marker:
                cutoff = min(cutoff, w["top"])
    return cutoff


def _read_pdf(path: str) -> pd.DataFrame | None:
    """Parse a Trade Republic-style PDF statement into a DataFrame with
    Date / Type / Description / Amount columns."""
    try:
        import pdfplumber
    except ImportError:
        raise ValueError(
            "Reading PDF statements requires installing a library. "
            "Run in a terminal:  pip install pdfplumber"
        )

    rows = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            all_words = page.extract_words(use_text_flow=False, keep_blank_chars=False)
            if not all_words:
                continue
            cutoff = _pdf_page_cutoff(all_words)
            words  = [w for w in all_words if w["top"] < cutoff]

            markers = sorted(
                w["top"] for w in words
                if _pdf_col(w["x0"]) == "date" and _PDF_DAY_RE.match(w["text"])
            )
            if not markers:
                continue

            for i, top in enumerate(markers):
                start = top - 0.5
                end   = markers[i + 1] - 0.5 if i + 1 < len(markers) else cutoff
                band_words = [w for w in words if start <= w["top"] < end]
                if not band_words:
                    continue

                fields = {"date": [], "type": [], "desc": [], "in": [], "out": []}
                for w in sorted(band_words, key=lambda w: (round(w["top"], 1), w["x0"])):
                    col = _pdf_col(w["x0"])
                    if col in fields:
                        fields[col].append(w["text"])

                m = _PDF_DATE_RE.search(" ".join(fields["date"]))
                if not m:
                    continue
                day, mon_txt, year = m.groups()
                mon = _MESES_ES.get(mon_txt.lower())
                if not mon:
                    continue

                in_amt  = _pdf_amount(" ".join(fields["in"]))
                out_amt = _pdf_amount(" ".join(fields["out"]))
                amount  = in_amt if in_amt is not None else (
                    -out_amt if out_amt is not None else None)
                if amount is None:
                    continue

                type_txt = " ".join(fields["type"]).strip()
                desc_txt = " ".join(fields["desc"]).strip()
                description = f"{type_txt}: {desc_txt}" if desc_txt else type_txt

                rows.append({
                    "Date":        f"{year}-{mon:02d}-{int(day):02d}",
                    "Type":        type_txt,
                    "Description": description,
                    "Amount":      amount,
                })

    if not rows:
        return None
    return pd.DataFrame(rows)


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

    if ext == ".pdf":
        df = _read_pdf(path)
        if df is None or len(df) == 0:
            raise ValueError(
                "Could not extract any transaction from the PDF.\n"
                "This works with Trade Republic statements that have "
                "selectable text (not scanned). If it's a different kind "
                "of PDF, let me know so I can adjust the reader to its "
                "format."
            )
        best = {
            "df": df, "skip_rows": 0,
            "date_col": "Date", "amount_col": "Amount",
            "desc_col": "Description", "desc2_col": None,
            "scores": {}, "total_score": 15.0,   # -> confidence 1.0
        }
        return _build_result(best)

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
            "Could not detect the file's structure.\n"
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
            # Don't silently drop real money movements just because the bank
            # left the description blank — that's real data loss (it can
            # hide genuine income/expenses as if they never happened).
            # Keep the row with a placeholder so it still gets imported and
            # shows up for manual categorization.
            desc = "(No description)"
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
