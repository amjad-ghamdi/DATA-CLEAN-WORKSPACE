"""Bounded pages; search, sorting and missing filters use the complete dataset."""
import math
import polars as pl


def dataset_preview(frame, *, offset=0, limit=50, query="", sort_column=None,
                    descending=False, missing_column=None, columns=None):
    if offset < 0 or not 1 <= limit <= 200:
        raise ValueError("Offset must be nonnegative and page size must be between 1 and 200.")
    selected = frame.columns if columns is None else columns
    if not selected or len(selected) != len(set(selected)) or any(c not in frame.columns for c in selected):
        raise ValueError("Choose one or more existing, distinct columns.")
    if sort_column and sort_column not in frame.columns:
        raise ValueError("Unknown sort column.")
    if missing_column and missing_column != "*" and missing_column not in frame.columns:
        raise ValueError("Unknown missing-value filter column.")
    row_name = "__preview_row__"
    while row_name in frame.columns:
        row_name += "_"
    view = frame.with_row_index(row_name, offset=1)
    if query:
        view = view.filter(pl.any_horizontal([
            pl.col(c).cast(pl.String, strict=False).str.to_lowercase().str.contains(query.lower(), literal=True).fill_null(False)
            for c in frame.columns]))
    if missing_column:
        names = frame.columns if missing_column == "*" else [missing_column]
        view = view.filter(pl.any_horizontal([pl.col(c).is_null() for c in names]))
    matched = view.height
    if sort_column:
        view = view.sort(sort_column, descending=descending, nulls_last=True, maintain_order=True)
    page = view.slice(offset, limit)
    schema = [{"name": c, "dtype": str(frame[c].dtype), "missing": frame[c].null_count(),
               "empty_strings": int((frame[c] == "").sum()) if frame[c].dtype == pl.String else 0}
              for c in frame.columns]
    rows = [{key: str(value) if isinstance(value, float) and not math.isfinite(value) else value
             for key, value in row.items()} for row in page.select(selected).to_dicts()]
    return {"rows": rows, "row_numbers": page[row_name].to_list(), "columns": selected, "schema": schema,
            "total_rows": frame.height, "matched_rows": matched, "offset": offset, "limit": limit,
            "shown_rows": page.height, "scope": "full_dataset"}
