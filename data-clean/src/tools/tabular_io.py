from pathlib import Path

import pandas as pd
import polars as pl


SUPPORTED_EXTENSIONS = {".csv", ".xlsx", ".xls"}
LARGE_DATASET_THRESHOLD = 200_000


def is_supported_file(path_or_name: str | Path) -> bool:
    return Path(path_or_name).suffix.lower() in SUPPORTED_EXTENSIONS


def _collect_streaming(lazy_frame: pl.LazyFrame) -> pl.DataFrame:
    """Collect using Polars streaming, with compatibility for older releases."""
    try:
        return lazy_frame.collect(engine="streaming")
    except TypeError:
        return lazy_frame.collect(streaming=True)


def read_tabular(path_or_name: str | Path) -> pl.DataFrame:
    """Read source data using the fastest available path.

    CSV and internal Parquet files use Polars' lazy/streaming engine, which
    processes data in batches and can use multiple CPU threads internally.
    Excel still requires a full parse, so large Excel files are cached as
    Parquet after the first read.
    """
    path = Path(path_or_name)
    suffix = path.suffix.lower()

    if suffix == ".csv":
        return _collect_streaming(pl.scan_csv(path))

    if suffix == ".parquet":
        return _collect_streaming(pl.scan_parquet(path))

    if suffix in {".xlsx", ".xls"}:
        frame = pd.read_excel(path)
        frame = frame.astype(object).where(pd.notna(frame), None)
        return pl.DataFrame(frame.to_dict(orient="list"), strict=False)

    raise ValueError(f"Unsupported file type: {suffix}")


def internal_cache_path(source_path: str | Path) -> Path:
    source = Path(source_path)
    return source.with_name(f".{source.stem}.dataclean.parquet")


def write_internal_cache(df: pl.DataFrame, source_path: str | Path) -> Path:
    """Persist an internal Parquet copy for faster follow-up operations."""
    cache_path = internal_cache_path(source_path)
    df.write_parquet(cache_path, compression="zstd")
    return cache_path


def preferred_processing_source(source_path: str | Path) -> Path:
    """Prefer an existing internal Parquet cache over reparsing Excel."""
    source = Path(source_path)
    cached = internal_cache_path(source)
    return cached if cached.exists() else source


def output_extension_for(source_path: str | Path) -> str:
    """Preserve CSV; normalize Excel output to modern .xlsx."""
    return ".csv" if Path(source_path).suffix.lower() == ".csv" else ".xlsx"


def write_tabular(df: pl.DataFrame, output_path: str | Path) -> None:
    """Write Polars data to CSV, XLSX, or internal Parquet."""
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    if path.suffix.lower() == ".csv":
        df.write_csv(path)
        return

    if path.suffix.lower() == ".xlsx":
        pd.DataFrame(df.to_dicts()).to_excel(path, index=False, engine="openpyxl")
        return

    if path.suffix.lower() == ".parquet":
        df.write_parquet(path, compression="zstd")
        return

    raise ValueError(f"Unsupported output file type: {path.suffix.lower()}")
