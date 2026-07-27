"""
importar_excel.py — Importa un Excel ya clasificado al archivo global.

Uso:
    python importar_excel.py mi_excel.xlsx
    python importar_excel.py mi_excel.xlsx --source "MyInvestor"
"""

import sys
import argparse
import pandas as pd
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent))
from storage import save_to_global

# Formatos de fecha que se intentan automáticamente
DATE_FORMATS = [
    "%d-%m-%y",    # 28-04-26
    "%d-%m-%Y",    # 28-04-2026
    "%d/%m/%Y",    # 28/04/2026
    "%d/%m/%y",    # 28/04/26
    "%Y-%m-%d",    # 2026-04-28
]

def parse_date(raw) -> str | None:
    s = str(raw).strip().split(" ")[0]  # quita hora si la hay
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
            "No se pudo leer el archivo. Asegúrate de que es un .xlsx o .xls válido.\n"
            "Si es un .csv, renómbralo a .csv e impórtalo así:\n"
            "  python importar_excel.py archivo.csv"
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
            raise ValueError("No se pudo leer el CSV.")

    # Detectar si tiene cabecera (si la primera fila contiene palabras como Date/Fecha)
    first = " ".join(str(v).lower() for v in df.iloc[0].tolist())
    if any(w in first for w in ("date", "fecha", "amount", "importe", "category")):
        df.columns = df.iloc[0].tolist()
        df = df.iloc[1:].reset_index(drop=True)
    else:
        # Sin cabecera: asignamos por posición (col 0=fecha, 1=importe, 2=descripción, 3=categoría)
        df.columns = ["Date", "Amount", "Description", "Category"] + [f"extra_{i}" for i in range(len(df.columns) - 4)]

    df = df[["Date", "Amount", "Description", "Category"]].copy()
    df = df.dropna(subset=["Date", "Amount"])

    # Normalizar fechas
    df["Date"] = df["Date"].apply(parse_date)
    df = df[df["Date"].notna()].copy()

    # Normalizar importes
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
    parser = argparse.ArgumentParser(description="Importa Excel clasificado al archivo global.")
    parser.add_argument("file",            help="Excel a importar (.xlsx)")
    parser.add_argument("--source", "-s",  default="Imported", help='Etiqueta de cuenta (ej: "MyInvestor")')
    args = parser.parse_args()

    path = Path(args.file)
    if not path.exists():
        print(f"\nERROR: No se encuentra '{path}'\n")
        sys.exit(1)

    print(f"\nLeyendo {path}...")
    df = load_existing_excel(str(path))

    print(f"\n  {len(df)} filas encontradas")
    print(f"  Período: {df['Date'].min()} → {df['Date'].max()}")
    print(f"\nPrimeras filas:")
    print(df.head(5).to_string(index=False))

    print(f"\n¿Importar {len(df)} filas con source='{args.source}'? (s/n): ", end="")
    if input().strip().lower() != "s":
        print("Cancelado.")
        sys.exit(0)

    n = save_to_global(df, source=args.source)
    print(f"\n✅ Hecho. {n} filas nuevas añadidas a gastos_global.xlsx")


if __name__ == "__main__":
    main()
