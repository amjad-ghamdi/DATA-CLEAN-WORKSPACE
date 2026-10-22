from pathlib import Path
from typing import Optional

import pandas as pd
import polars as pl


SUPPORTED_EXTENSIONS = {".csv", ".xlsx", ".xls"}


def is_supported_file(path_or_name: str | Path) -> bool:
    return Path(path_or_name).suffix.lower() in SUPPORTED_EXTENSIONS


def read_tabular(path_or_name: str | Path) -> pl.DataFrame:
    """Read CSV or Excel into a Polars DataFrame with consistent null handling."""
    path = Path(path_or_name)
    suffix = path.suffix.lower()

    if suffix == ".csv":
        return pl.read_csv(path)

    if suffix in {".xlsx", ".xls"}:
        frame = pd.read_excel(path)
        frame = frame.astype(object).where(pd.notna(frame), None)
        return pl.DataFrame(frame.to_dict(orient="list"), strict=False)

    raise ValueError(f"Unsupported file type: {suffix}")


def output_extension_for(source_path: str | Path) -> str:
    """Preserve CSV; normalize Excel output to modern .xlsx."""
    return ".csv" if Path(source_path).suffix.lower() == ".csv" else ".xlsx"


def write_tabular(df: pl.DataFrame, output_path: str | Path) -> None:
    """Write Polars data to CSV or XLSX without requiring PyArrow conversion."""
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    if path.suffix.lower() == ".csv":
        df.write_csv(path)
        return

    if path.suffix.lower() == ".xlsx":
        pd.DataFrame(df.to_dicts()).to_excel(path, index=False, engine="openpyxl")
        return

    raise ValueError(f"Unsupported output file type: {path.suffix.lower()}")
