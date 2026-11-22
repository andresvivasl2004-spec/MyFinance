"""
storage.py — Manages the global transaction Excel file.

The global file (gastos_global.xlsx) is the single source of truth.
Each time you categorise a new bank export, you append it here.
Duplicate detection ensures running the same month twice never creates double entries.

Typical notebook workflow
-------------------------
    # Step 1 – categorise the raw bank file (categorizer.py)
    out_csv = categorize_csv("may.xlsx", bank="bbva")

    # Step 2 – review in the notebook
    df = review(out_csv)
    display(df)           # inspect, edit categories if needed

    # Step 3 – append to global file (this module)
    n = save_to_global(out_csv, source="BBVA")
    print(f"{n} new rows added")

    # Step 4 – reload all_data for analysis
    all_data = load_from_global()
"""

import os
import shutil
from datetime import datetime
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


def _dedup_keys(df: pd.DataFrame) -> list[tuple]:
    """
    Build the (Date, Amount, Description, Source) key used to spot duplicates.

    Reading gastos_global.xlsx back with pandas doesn't reliably give plain
    "YYYY-MM-DD" strings for the Date column: once a cell has ever been
    written as a real Excel date (which openpyxl/pandas do for a datetime64
    column), pandas parses it back as a Timestamp and str()-ing it produces
    "YYYY-MM-DD 00:00:00" — one row from a freshly-parsed bank file ("Date":
    "2022-03-06") would then never match the same row already sitting in the
    global file ("2022-03-06 00:00:00"), so duplicate detection silently
    failed for the entire existing file. Parsing with pd.to_datetime and
    reformatting explicitly makes both sides land on the same string
    regardless of which shape they arrived in.

    Description is also lower-cased and stripped, since the same transaction
    re-parsed from a different export format (CSV vs PDF, etc.) can come back
    with different casing/whitespace even though it's the same real-world
    movement.

    NOTE: this used to call pd.to_datetime(df["Date"]).dt.strftime(...) to
    normalise both shapes. That's broken for a column that mixes the two
    shapes ("2022-03-06 00:00:00" AND "2022-03-06" in the same Series, which
    is exactly what gastos_global.xlsx contains once old rows and freshly
    appended rows sit side by side): pandas' vectorised datetime parser
    infers ONE format from the column and silently turns every value that
    doesn't match it into NaT, rather than parsing each value on its own —
    so half the existing keys came out as NaT and could never match anything,
    letting duplicates back in. Both shapes always start with "YYYY-MM-DD"
    though, so plain string slicing sidesteps the whole format-inference
    problem instead of relying on a datetime parser to guess consistently.
    """
    date = df["Date"].astype(str).str.strip().str[:10]
    amount = df["Amount"].astype(float).round(2)
    desc = df["Description"].astype(str).str.strip().str.lower()
    source = df["Source"].astype(str).str.strip()
    return list(zip(date, amount, desc, source))


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
        existing_keys = set(_dedup_keys(existing_df))
        new_keys = _dedup_keys(new_df)
        mask = [k not in existing_keys for k in new_keys]
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


BACKUP_DIR   = "backups"
BACKUP_KEEP  = 12


def backup_paths(path: Path) -> list[Path]:
    """Existing backups for `path`, newest first."""
    folder = Path(path).resolve().parent / BACKUP_DIR
    if not folder.is_dir():
        return []
    stem = Path(path).stem
    return sorted(folder.glob(f"{stem}_*.xlsx"), reverse=True)


def _snapshot_existing(path: Path) -> Path | None:
    """Copy the CURRENT file aside before it is replaced, and prune old copies.

    Returns the backup path, or None if there was nothing to back up yet.
    """
    path = Path(path)
    if not path.exists():
        return None
    folder = path.resolve().parent / BACKUP_DIR
    folder.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    # Never write over an existing backup. Names are per-second, and two saves
    # inside one second do happen (a restore snapshots immediately after the
    # write that prompted it) — without this the second copy would silently
    # destroy the first, which is the one you actually wanted.
    dest = folder / f"{path.stem}_{stamp}{path.suffix}"
    n = 1
    while dest.exists():
        dest = folder / f"{path.stem}_{stamp}-{n}{path.suffix}"
        n += 1
    shutil.copy2(path, dest)
    for old_backup in backup_paths(path)[BACKUP_KEEP:]:
        try:
            old_backup.unlink()
        except OSError:
            pass          # a locked or vanished backup is not worth failing over
    return dest


def describe_backups(path: Path = None) -> list[dict]:
    """Backups for `path`, newest first, each with when it was taken and how many
    transactions it holds — so the restore UI can show what it is offering rather
    than a list of filenames."""
    path = Path(path or GLOBAL_FILE)
    out = []
    for bk in backup_paths(path):
        try:
            rows = len(pd.read_excel(bk, sheet_name=SHEET_NAME))
        except Exception:
            rows = None               # unreadable backup: still list it, flagged
        stamp = bk.stem.rsplit("_", 2)[-2:]          # ["20260911", "105237"]
        try:                                         # or "105237-1" on a collision
            when = datetime.strptime("_".join(stamp).split("-")[0] if len(stamp) == 2
                                     else "", "%Y%m%d_%H%M%S")
        except ValueError:
            when = datetime.fromtimestamp(bk.stat().st_mtime)
        out.append({"path": bk, "when": when, "rows": rows,
                    "kb": bk.stat().st_size / 1024})
    return out


def restore_backup(backup: Path, path: Path = None) -> int:
    """
    Put a backup back in place, and return how many transactions it contains.

    The current file is snapshotted first, so restoring is itself undoable: if
    you restore the wrong one, the state you just left is the newest backup. The
    backup is read and checked before anything is moved, so pointing this at a
    corrupt file raises instead of overwriting good data with bad.
    """
    backup, path = Path(backup), Path(path or GLOBAL_FILE)
    rows = len(pd.read_excel(backup, sheet_name=SHEET_NAME))   # raises if bad
    tmp = path.with_name(f".{path.stem}.restore{path.suffix}")
    try:
        # Copy the backup out of backups/ BEFORE snapshotting the current file,
        # so that nothing the snapshot does to that folder can affect what is
        # about to be restored.
        shutil.copy2(backup, tmp)
        _snapshot_existing(path)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
    return rows


def _write_excel(df: pd.DataFrame, path: Path) -> None:
    """
    Write the master file so that a failure cannot destroy the previous one.

    The whole transaction history lives in this single workbook, and the naive
    version of this function opened it directly for writing: an exception or a
    killed process partway through left a truncated file and nothing to recover
    from. (Not hypothetical — it happened during development, and only a manual
    copy saved it.)

    So the order here is deliberate:

      1. copy the current file into backups/ (keeping the last BACKUP_KEEP)
      2. write the new workbook to a temporary file beside it
      3. READ THAT TEMPORARY FILE BACK and check it parses and has the expected
         row count — a workbook that openpyxl produced but cannot reopen is
         exactly the failure that silently eats your data
      4. only then os.replace() it over the real path, which is atomic, so the
         file at that path is always either the old one or the new one and
         never a half-written mixture

    The cost is one extra file copy per save. The alternative is losing years of
    transactions to a crash at the wrong moment.
    """
    path = Path(path)
    _snapshot_existing(path)
    tmp = path.with_name(f".{path.stem}.tmp{path.suffix}")
    try:
        _write_excel_body(df, tmp)
        check = pd.read_excel(tmp, sheet_name=SHEET_NAME)
        if len(check) != len(df):
            raise ValueError(
                f"verification failed: wrote {len(df)} rows, read back {len(check)}")
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def _write_excel_body(df: pd.DataFrame, path: Path) -> None:
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
# DUPLICATE REVIEW
# ─────────────────────────────────────────────────────────────────────────────

def group_key(row: tuple) -> tuple:
    """
    The (Date, Amount, Description, Source) identity used to group and
    dismiss possible-duplicate transactions — same normalisation as
    _dedup_keys, applied to a single (date, amount, description, category,
    source) tuple as returned by load_from_global().
    """
    dt, amount, desc, _cat, source = row
    return (
        dt.strftime("%Y-%m-%d"),
        round(float(amount), 2),
        str(desc).strip().lower(),
        str(source).strip(),
    )


def find_duplicate_groups(
    all_data: list[tuple],
    dismissed: set[tuple] | None = None,
) -> list[list[tuple]]:
    """
    Group already-saved transactions that share the same (Date, Amount,
    Description, Source) key — the same key save_to_global() uses to reject
    new rows. Anything with more than one member here is either a genuine
    repeated transaction (e.g. two identical bus fares the same day) or a
    leftover from a re-import that slipped past duplicate detection.

    Parameters
    ----------
    all_data  : the (date, amount, description, category, source) tuples as
                returned by load_from_global()/processor's data pipeline.
    dismissed : group keys (see group_key()) the user has already reviewed
                and confirmed are genuine, not accidental duplicates — pass
                duplicates.load_dismissed() to hide those from the result.

    Returns
    -------
    A list of groups (each a list of the original tuples, ≥2 items), sorted
    newest-first by date. Only real, non-dismissed duplicates are returned —
    nothing is deleted or modified here, this is read-only for the user to
    review (dismissing a group only hides it from this list; it doesn't
    touch the underlying rows).
    """
    groups: dict[tuple, list[tuple]] = {}
    for row in all_data:
        groups.setdefault(group_key(row), []).append(row)

    dismissed = dismissed or set()
    dupes = [rows for key, rows in groups.items() if len(rows) > 1 and key not in dismissed]
    dupes.sort(key=lambda rows: rows[0][0], reverse=True)
    return dupes


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


# ─────────────────────────────────────────────────────────────────────────────
# EDITING EXISTING TRANSACTIONS
# ─────────────────────────────────────────────────────────────────────────────

def transaction_rows(global_file: str = GLOBAL_FILE) -> list[dict]:
    """
    Every stored transaction, with the sheet row number that holds it.

    The row number is what makes editing possible at all: this file has no id
    column, and (date, amount, description) is not unique — the same €10 metro
    top-up on the same day is a perfectly ordinary thing to do twice. Carrying
    the physical row means an edit can name exactly one transaction.
    """
    path = Path(global_file)
    if not path.exists():
        return []
    wb = load_workbook(path, read_only=True)
    ws = wb[SHEET_NAME]
    out, header = [], None
    for i, row in enumerate(ws.iter_rows(values_only=True), start=1):
        if i == 1:
            header = [str(c) for c in row]
            continue
        if row is None or all(c is None for c in row):
            continue
        rec = dict(zip(header, row))
        d = rec.get("Date")
        out.append({
            "row": i,
            "Date": (d.strftime("%Y-%m-%d") if hasattr(d, "strftime")
                     else str(d)[:10]),
            "Amount": float(rec.get("Amount") or 0.0),
            "Description": str(rec.get("Description") or ""),
            "Category": str(rec.get("Category") or ""),
            "Source": str(rec.get("Source") or ""),
        })
    wb.close()
    return out


def update_categories(updates: list[dict], global_file: str = GLOBAL_FILE) -> int:
    """
    Change the Category of specific rows, in place, and return how many changed.

    `updates` is [{"row": int, "expect": {"Date","Amount","Description"},
    "category": str}, ...].

    Every row is VERIFIED against what the caller thought was there before a
    single cell is written, and a single mismatch aborts the whole edit. This is
    not paranoia about the code: the editor shows a snapshot, and between the
    snapshot and the save the file can be re-sorted by an import in another tab,
    or restored from a backup. Writing "Groceries" onto whatever now occupies
    row 412 would corrupt a transaction silently and in a way no later
    inspection could untangle. Refusing is always recoverable; guessing isn't.

    The write itself reuses the same discipline as every other write here —
    snapshot to backups/, build the new file beside the old one, read it back
    and check it parses, then swap atomically.
    """
    updates = [u for u in updates if u.get("category")]
    if not updates:
        return 0
    path = Path(global_file)
    if not path.exists():
        raise FileNotFoundError(path)

    _snapshot_existing(path)
    tmp = path.with_name(f".{path.stem}.edit{path.suffix}")
    try:
        shutil.copy2(path, tmp)
        wb = load_workbook(tmp)
        ws = wb[SHEET_NAME]
        header = [str(c.value) for c in ws[1]]
        col = {name: header.index(name) + 1 for name in
               ("Date", "Amount", "Description", "Category")}

        changed = 0
        for u in updates:
            r = int(u["row"])
            if r < 2 or r > ws.max_row:
                raise ValueError(f"row {r} is outside the sheet")
            exp = u.get("expect") or {}
            d = ws.cell(r, col["Date"]).value
            got = {
                "Date": (d.strftime("%Y-%m-%d") if hasattr(d, "strftime")
                         else str(d)[:10]),
                "Amount": round(float(ws.cell(r, col["Amount"]).value or 0.0), 2),
                "Description": str(ws.cell(r, col["Description"]).value or ""),
            }
            for k, v in exp.items():
                mine = round(float(v), 2) if k == "Amount" else str(v)
                if got.get(k) != mine:
                    raise ValueError(
                        f"row {r} is not the transaction you edited "
                        f"({k}: file has {got.get(k)!r}, you had {mine!r}). "
                        "Nothing was changed — reload and try again.")
            cell = ws.cell(r, col["Category"])
            if str(cell.value or "") != u["category"]:
                cell.value = u["category"]
                changed += 1

        if not changed:
            return 0
        wb.save(tmp)
        check = pd.read_excel(tmp, sheet_name=SHEET_NAME)
        if len(check) != ws.max_row - 1:
            raise ValueError(
                f"verification failed: sheet has {ws.max_row - 1} rows, "
                f"read back {len(check)}")
        os.replace(tmp, path)
        return changed
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
