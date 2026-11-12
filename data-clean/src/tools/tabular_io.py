"""Strict reading with explicit, reusable interpretation options."""
import codecs
import csv
from datetime import date, datetime, time
import io
from pathlib import Path
import re

from charset_normalizer import from_bytes
import pandas as pd
import polars as pl

SUPPORTED_EXTENSIONS = {".csv", ".xlsx", ".xls"}
LARGE_DATASET_THRESHOLD = 200_000


class TabularReadError(ValueError):
    def __init__(self, message, issues=None):
        super().__init__(message)
        self.issues = issues or []


def is_supported_file(path_or_name):
    return Path(path_or_name).suffix.lower() in SUPPORTED_EXTENSIONS


def _collect_streaming(lazy_frame):
    try:
        return lazy_frame.collect(engine="streaming")
    except TypeError:
        return lazy_frame.collect(streaming=True)


def _encoding(path, selected):
    if selected:
        try:
            return codecs.lookup(selected).name, []
        except LookupError as exc:
            raise TabularReadError("Unknown text encoding. Select another encoding.") from exc
    with path.open("rb") as handle:
        sample = handle.read(131072)
    if sample.startswith((b"\xff\xfe", b"\xfe\xff")):
        return "utf-16", []
    try:
        codecs.getincrementaldecoder("utf-8-sig")().decode(sample, final=False)
        return "utf-8-sig", []
    except UnicodeDecodeError:
        match = from_bytes(sample).best()
        if match is None:
            raise TabularReadError("Encoding could not be detected. Select it manually.")
        return match.encoding, ["Encoding was suggested automatically. Verify names and non-English text."]


def _headers(values):
    names = [str(value) if value is not None else "" for value in values]
    if any(not name.strip() for name in names):
        raise TabularReadError("The header contains empty column names. Choose the correct header row or fix the source.")
    if len(set(names)) != len(names):
        raise TabularReadError("The header contains duplicate column names. Fix the source header.")
    return names


def _infer_safely(frame):
    expressions, protected = [], []
    for name in frame.columns:
        series = frame[name]
        if series.dtype != pl.String or series.drop_nulls().len() == 0:
            continue
        values = series.drop_nulls()
        identifier = bool(re.search(r"(^|[\s_\-])(id|identifier|code|phone|mobile|postal|zip|sku|account)([\s_\-]|$)", name.lower()))
        numeric_text = values.str.contains(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$")
        precision_sensitive = (numeric_text & (values.str.replace_all(r"[^0-9]", "").str.len_chars() > 15)).any()
        unclear_date = values.str.contains(r"^\s*\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4}\s*$").any()
        formatted_number = values.str.contains(r"\d,\d").any()
        if identifier or precision_sensitive or unclear_date or formatted_number or values.str.contains(r"^[+-]?0\d").any():
            protected.append(name)
            continue
        if values.str.contains(r"^-?(0|[1-9]\d*)$").all():
            cast = series.cast(pl.Int64, strict=False)
            if cast.null_count() == series.null_count():
                expressions.append(pl.col(name).cast(pl.Int64))
        elif values.str.contains(r"^[+-]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][+-]?\d+)?$").all():
            digits = values.str.replace_all(r"[^0-9]", "").str.len_chars().max()
            if digits is not None and digits <= 15:
                cast = series.cast(pl.Float64, strict=False)
                if cast.null_count() == series.null_count() and cast.drop_nulls().is_finite().all():
                    expressions.append(pl.col(name).cast(pl.Float64))
    return (frame.with_columns(expressions) if expressions else frame), protected


def read_with_options(path_or_name, options=None):
    path, options = Path(path_or_name), dict(options or {})
    suffix, header_row = path.suffix.lower(), options.get("header_row", 1)
    if type(header_row) is not int or not 1 <= header_row <= 10000:
        raise TabularReadError("Header row must be between 1 and 10000.")
    warnings, sheets, blank_records = [], [], 0
    if suffix == ".csv":
        encoding, warnings = _encoding(path, options.get("encoding"))
        try:
            with path.open(encoding=encoding, errors="strict", newline="") as handle:
                text = handle.read()
        except (UnicodeError, LookupError) as exc:
            raise TabularReadError("This encoding cannot decode the file. Select another encoding; no rows were skipped.") from exc
        if not text.strip():
            raise TabularReadError("The file is empty or contains no data.")
        lines = io.StringIO(text, newline="").readlines()
        delimiter = options.get("delimiter")
        if delimiter is not None and delimiter not in {",", ";", "\t", "|"}:
            raise TabularReadError("Choose comma, semicolon, tab, or pipe as the delimiter.")
        if delimiter is None:
            sample = "".join(lines[header_row - 1:])[:65536]
            try:
                delimiter = csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
            except csv.Error:
                delimiter = ","
                warnings.append("Delimiter detection was inconclusive. Verify the columns or select a delimiter.")
        reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter, strict=True)
        issues, blank_lines = [], set()
        try:
            for _ in range(header_row - 1):
                next(reader)
            names = _headers(next(reader))
            header_end, data_rows = reader.line_num, 0
            for record in reader:
                if not record:
                    blank_records += 1
                    blank_lines.add(reader.line_num - 1)
                    continue
                data_rows += 1
                if len(record) != len(names) and len(issues) < 20:
                    issues.append({"line": reader.line_num, "expected_fields": len(names), "actual_fields": len(record)})
            if issues:
                raise TabularReadError("Inconsistent field counts. Check the delimiter/header or fix the source; no records were silently skipped. Showing up to 20 errors.", issues)
            if not data_rows:
                raise TabularReadError("The file has a header but no data rows.")
        except StopIteration as exc:
            raise TabularReadError("The header row is beyond the end of the file.") from exc
        except csv.Error as exc:
            raise TabularReadError("Invalid CSV quoting. Fix the source; no records were skipped.", [{"line": reader.line_num, "reason": str(exc)}]) from exc
        body = "".join(line for i, line in enumerate(lines) if i >= header_end and i not in blank_lines)
        try:
            frame = pl.read_csv(io.StringIO(body), has_header=False, new_columns=names,
                                separator=delimiter, infer_schema=False, try_parse_dates=False,
                                ignore_errors=False, truncate_ragged_lines=False)
        except Exception as exc:
            raise TabularReadError(f"CSV parsing failed without skipping records: {exc}") from exc
        if frame.width != len(names) or frame.height != data_rows:
            raise TabularReadError("CSV parsers disagree about the structure. Fix quoting or delimiter before analysis.")
        resolved = {"delimiter": delimiter, "encoding": encoding, "header_row": header_row, "sheet_name": None}
        if len(names) == 1:
            warnings.append("Only one column was read. If unexpected, change the delimiter and update the preview.")
        if blank_records:
            warnings.append(f"Ignored {blank_records} completely blank CSV record(s). Delimited records with empty fields remain.")
    elif suffix in {".xlsx", ".xls"}:
        try:
            with pd.ExcelFile(path) as workbook:
                sheets = workbook.sheet_names
                sheet = options.get("sheet_name") or sheets[0]
                if sheet not in sheets:
                    raise TabularReadError("The selected Excel worksheet does not exist.")
                raw = pd.read_excel(workbook, sheet_name=sheet, header=None, dtype=object, keep_default_na=False)
            if len(raw.index) <= header_row:
                raise TabularReadError("The selected worksheet/header has no data rows.")
            names = _headers(raw.iloc[header_row - 1].tolist())
            def display(value):
                if value is None or value == "" or pd.isna(value):
                    return None
                return value.isoformat() if isinstance(value, (datetime, date, time)) else str(value)
            frame = pl.DataFrame({name: [display(v) for v in raw.iloc[header_row:, i].tolist()]
                                  for i, name in enumerate(names)}, schema={name: pl.String for name in names})
        except TabularReadError:
            raise
        except Exception as exc:
            raise TabularReadError(f"Excel reading failed. Check the file and worksheet: {exc}") from exc
        resolved = {"delimiter": None, "encoding": None, "header_row": header_row, "sheet_name": sheet}
        warnings.append("Excel display formats are not reapplied. Zeros stored only as a numeric display format cannot be recovered automatically.")
    else:
        raise TabularReadError(f"Unsupported file type: {suffix}")
    frame, protected = _infer_safely(frame)
    if protected:
        warnings.append("Kept identifier-like, precision-sensitive or ambiguous-format columns as text and flagged their transformations for review: " + ", ".join(protected))
    warnings.append("Dates and formatted numbers remain text when interpretation is ambiguous. Previewing does not clean or overwrite the source.")
    return frame, {"reading_options": resolved, "worksheets": sheets, "warnings": warnings,
                   "blank_records_ignored": blank_records, "protected_text_columns": protected}


def read_tabular(path_or_name, options=None):
    path = Path(path_or_name)
    if path.suffix.lower() == ".parquet":
        return _collect_streaming(pl.scan_parquet(path))
    return read_with_options(path, options)[0]


def internal_cache_path(source_path):
    source = Path(source_path)
    return source.with_name(f".{source.stem}.dataclean.parquet")


def write_internal_cache(df, source_path):
    cache_path = internal_cache_path(source_path)
    temporary = cache_path.with_suffix(".pending.parquet")
    df.write_parquet(temporary, compression="zstd")
    temporary.replace(cache_path)
    return cache_path


def preferred_processing_source(source_path):
    source = Path(source_path)
    cached = internal_cache_path(source)
    return cached if cached.exists() else source


def output_extension_for(source_path):
    return ".csv" if Path(source_path).suffix.lower() == ".csv" else ".xlsx"


def write_tabular(df, output_path):
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix.lower() == ".csv":
        df.write_csv(path)
    elif path.suffix.lower() == ".xlsx":
        pd.DataFrame(df.to_dicts()).to_excel(path, index=False, engine="openpyxl")
    elif path.suffix.lower() == ".parquet":
        df.write_parquet(path, compression="zstd")
    else:
        raise ValueError(f"Unsupported output file type: {path.suffix.lower()}")
