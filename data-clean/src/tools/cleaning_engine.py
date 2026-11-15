import json
import csv
from pathlib import Path
from typing import Any, Dict, List, Optional

import polars as pl

from .tabular_io import read_tabular, write_tabular, conversion_review


class CleaningEngine:
    """Execute deterministic cleaning actions with vectorized lineage tracking."""

    NULL_TOKENS = ["", "na", "n/a", "null", "none"]

    AUDIT_SCHEMA = {"action": pl.String, "column": pl.String, "row_index": pl.String,
                    "source_row_id": pl.UInt64, "operation": pl.UInt32, "change_kind": pl.String,
                    "old_value": pl.String, "new_value": pl.String, "old_is_null": pl.Boolean,
                    "new_is_null": pl.Boolean, "method": pl.String, "impact_count": pl.Int64}

    def __init__(self, file_path: str, audit_path=None):
        self.file_path = Path(file_path)
        self.df: Optional[pl.DataFrame] = None
        self.change_log: List[Dict[str, Any]] = []
        self.lineage_limit = None
        self._source_rows = None
        self.change_totals = {"filled": 0, "modified": 0, "cleared": 0, "removed": 0}
        self._operation = 0
        self.audit_path = Path(audit_path) if audit_path else None
        self.audit_entries = 0
        self._audit_summary = {}
        self._changed_sources = bytearray()
        if self.audit_path:
            with self.audit_path.open('w', encoding='utf-8', newline='') as handle:
                csv.writer(handle).writerow(self.AUDIT_SCHEMA)

    def _append_records(self, records):
        if not records:
            return
        if not self.audit_path:
            self.change_log.extend(records)
            return
        if not self._changed_sources:
            self._changed_sources = bytearray(int(self._source_rows.max() or 0))
        with self.audit_path.open('a', encoding='utf-8', newline='') as handle:
            writer = csv.DictWriter(handle, fieldnames=self.AUDIT_SCHEMA, quoting=csv.QUOTE_NONNUMERIC)
            for record in records:
                writer.writerow({k: str(v).lower() if isinstance(v, bool) else v for k,v in record.items()})
                self.audit_entries += 1
                key = record['action'], record['column']
                self._audit_summary[key] = self._audit_summary.get(key, 0) + record.get('impact_count', 1)
                if record['change_kind'] != 'removed':
                    self._changed_sources[record['source_row_id']-1] = 1
                if len(self.change_log) < 20:
                    self.change_log.append(record)

    def iter_audit(self):
        if not self.audit_path:
            yield from self.change_log
            return
        with self.audit_path.open(encoding='utf-8', newline='') as handle:
            for record in csv.DictReader(handle):
                for key in ('source_row_id','operation','impact_count'):
                    record[key] = int(record[key])
                for key in ('old_is_null','new_is_null'):
                    record[key] = record[key] == 'true'
                yield record

    def preview_audit(self, rows):
        if not self.audit_path:
            return [record for record in self.change_log if record.get('source_row_id') in rows]
        return (pl.scan_csv(self.audit_path, schema=self.AUDIT_SCHEMA)
                .filter(pl.col('source_row_id').is_in(list(rows)))
                .collect(engine='streaming').to_dicts())

    @property
    def log_entries(self):
        return self.audit_entries if self.audit_path else len(self.change_log)

    def export_audit(self, path):
        if self.audit_path:
            self.audit_path.replace(path)
            self.audit_path = Path(path)
        else:
            self.get_lineage_df().write_csv(path)

    def load_data(self) -> bool:
        try:
            self.df = read_tabular(self.file_path)
            return True
        except Exception as exc:
            print(f"Error loading file for cleaning: {exc}")
            return False

    def _require_data(self) -> None:
        if self.df is None:
            raise ValueError("Dataset is not loaded.")
        if self._source_rows is None:
            self._source_rows = pl.Series("source_row_id", range(1, self.df.height + 1), dtype=pl.UInt64)
        if len(self._source_rows) != self.df.height:
            raise ValueError("Row identity tracking is inconsistent with the dataset.")

    def _require_column(self, column: str) -> None:
        self._require_data()
        if column not in self.df.columns:
            raise ValueError(f"Column '{column}' was not found in the dataset.")

    def _log_change(
        self,
        action: str,
        column: str,
        row_idx: Any,
        old_val: Any,
        new_val: Any,
        impact_count: int = 1,
    ) -> None:
        if self.lineage_limit is not None and len(self.change_log) >= self.lineage_limit:
            return
        self.change_log.append(
            {
                "action": action,
                "column": column,
                "row_index": row_idx,
                "old_value": old_val,
                "new_value": new_val,
                "impact_count": impact_count,
            }
        )

    def _log_column_changes(
        self,
        action: str,
        column: str,
        before: pl.Series,
    ) -> int:
        """Record only changed rows using Polars instead of Python row loops."""
        self._require_column(column)
        after = self.df[column]

        old_value = pl.col("old_value")
        new_value = pl.col("new_value")
        if before.dtype != after.dtype and (
            before.dtype == pl.Utf8 or after.dtype == pl.Utf8
        ):
            # Parsing changes a string column to a numeric/temporal dtype.
            # Compare display values without asking Polars to compare a
            # temporal column directly with strings. Keep the native Series
            # intact for output and use native equality for numeric promotion.
            old_value = old_value.cast(pl.Utf8, strict=False)
            new_value = new_value.cast(pl.Utf8, strict=False)

        changes = (
            pl.DataFrame(
                {
                    "old_value": before,
                    "new_value": after,
                    "source_row_id": self._source_rows,
                }
            )
            .with_row_index("row_index")
            .filter(~old_value.eq_missing(new_value))
        )

        changed = changes.height
        if changed == 0:
            return 0

        kinds = changes.select(
            pl.col("old_value").is_null().sum().alias("filled"),
            pl.col("new_value").is_null().sum().alias("cleared"),
        ).row(0)
        self.change_totals["filled"] += int(kinds[0])
        self.change_totals["cleared"] += int(kinds[1])
        self.change_totals["modified"] += changed - int(kinds[0]) - int(kinds[1])
        self._operation += 1
        if self.lineage_limit is not None:
            changes = changes.head(max(0, self.lineage_limit - len(self.change_log)))
        records = changes.select(
            [
                pl.lit(action).alias("action"),
                pl.lit(column).alias("column"),
                (pl.col("source_row_id") - 1).cast(pl.Utf8).alias("row_index"),
                pl.col("source_row_id"),
                pl.lit(self._operation).alias("operation"),
                pl.when(pl.col("old_value").is_null()).then(pl.lit("filled"))
                .when(pl.col("new_value").is_null()).then(pl.lit("cleared"))
                .otherwise(pl.lit("modified")).alias("change_kind"),
                pl.col("old_value").is_null().alias("old_is_null"),
                pl.col("new_value").is_null().alias("new_is_null"),
                pl.lit(action).alias("method"),
                pl.col("old_value")
                .cast(pl.Utf8, strict=False)
                .fill_null("")
                .alias("old_value"),
                pl.col("new_value")
                .cast(pl.Utf8, strict=False)
                .fill_null("")
                .alias("new_value"),
                pl.lit(1, dtype=pl.Int64).alias("impact_count"),
            ]
        )
        for batch in records.iter_slices(4096):
            self._append_records(batch.to_dicts())
        return changed

    def map_categories(self, column: str, mapping: Dict[str, str], approved: bool = False) -> int:
        self._require_column(column)
        if not approved:
            raise ValueError("Confirm equivalent meaning before mapping categories.")
        if self.df[column].dtype != pl.String:
            raise ValueError("Category mappings require a text column.")
        if not mapping or any(not isinstance(k, str) or not isinstance(v, str) or not v.strip()
                              or len(k) > 1000 or len(v) > 1000 for k, v in mapping.items()):
            raise ValueError("Provide a non-empty, bounded text mapping.")
        # A single simultaneous replacement prevents A→B→C cascading.
        before = self.df[column].clone()
        self.df = self.df.with_columns(pl.col(column).replace(mapping).alias(column))
        return self._log_column_changes("map_categories", column, before)

    def trim_whitespace(self, columns: List[str]) -> int:
        self._require_data()
        total_changed = 0

        for column in columns:
            self._require_column(column)
            if self.df[column].dtype != pl.Utf8:
                continue

            before = self.df[column].clone()
            self.df = self.df.with_columns(
                pl.col(column).str.strip_chars().alias(column)
            )
            total_changed += self._log_column_changes(
                "trim_whitespace",
                column,
                before,
            )

        return total_changed

    def standardize_null_tokens(
        self,
        column: str,
        tokens: Optional[List[str]] = None,
    ) -> int:
        self._require_column(column)
        if self.df[column].dtype != pl.Utf8:
            return 0

        normalized_tokens = [
            token.lower()
            for token in (tokens or self.NULL_TOKENS)
        ]
        before = self.df[column].clone()

        self.df = self.df.with_columns(
            pl.when(
                pl.col(column)
                .str.strip_chars()
                .str.to_lowercase()
                .is_in(normalized_tokens)
            )
            .then(None)
            .otherwise(pl.col(column))
            .alias(column)
        )

        return self._log_column_changes(
            "standardize_null_tokens",
            column,
            before,
        )

    def normalize_casing(
        self,
        column: str,
        target_case: str = "lower",
    ) -> int:
        self._require_column(column)
        if self.df[column].dtype != pl.Utf8:
            return 0

        before = self.df[column].clone()

        if target_case == "lower":
            expression = pl.col(column).str.to_lowercase()
        elif target_case == "upper":
            expression = pl.col(column).str.to_uppercase()
        elif target_case == "title":
            expression = pl.col(column).str.to_titlecase()
        else:
            raise ValueError(f"Unsupported target_case: {target_case}")

        self.df = self.df.with_columns(expression.alias(column))
        return self._log_column_changes(
            "normalize_casing",
            column,
            before,
        )

    def fill_nulls(
        self,
        column: str,
        strategy: str,
        fill_value: Any = None,
    ) -> int:
        self._require_column(column)
        strategy = strategy.lower()

        if strategy == "keep":
            return 0

        if strategy == "drop":
            return self._remove_rows(self.df[column].is_null(), "drop_missing_rows", column)

        before = self.df[column].clone()

        if strategy == "constant":
            if fill_value is None:
                raise ValueError(
                    "fill_value is required for constant imputation."
                )
            self.df = self.df.with_columns(
                pl.col(column).fill_null(fill_value).alias(column)
            )

        elif strategy == "mean":
            value = self.df[column].mean()
            if value is None:
                return 0
            self.df = self.df.with_columns(
                pl.col(column).fill_null(value).alias(column)
            )

        elif strategy == "median":
            value = self.df[column].median()
            if value is None:
                return 0
            self.df = self.df.with_columns(
                pl.col(column).fill_null(value).alias(column)
            )

        elif strategy == "mode":
            mode_result = self.df[column].drop_nulls().mode()
            if mode_result.len() == 0:
                return 0
            if mode_result.len() > 1:
                raise ValueError(f"{column}: most-common values are tied. Choose a constant or keep missing values.")
            value = mode_result[0]
            self.df = self.df.with_columns(
                pl.col(column).fill_null(value).alias(column)
            )

        else:
            raise ValueError(
                f"Unsupported missing-value strategy: {strategy}"
            )

        return self._log_column_changes(
            f"fill_nulls_{strategy}",
            column,
            before,
        )

    def contextual_fill(
        self,
        column: str,
        context_column: str,
        context_values: List[str],
        statistic: str = "median",
        round_result: bool = False,
    ) -> int:
        self._require_column(column)
        self._require_column(context_column)

        context_values_lower = [
            value.lower()
            for value in context_values
        ]
        context_expr = (
            pl.col(context_column)
            .cast(pl.Utf8)
            .str.to_lowercase()
            .str.contains("|".join(context_values_lower))
        )

        eligible = (
            self.df
            .filter(context_expr)
            .select(column)
            .drop_nulls()
        )
        if eligible.height == 0:
            return 0

        series = eligible[column]
        value = (
            series.mean()
            if statistic == "mean"
            else series.median()
        )
        if value is None:
            return 0

        if round_result:
            value = int(round(float(value)))

        before = self.df[column].clone()
        self.df = self.df.with_columns(
            pl.when(
                context_expr
                & pl.col(column).is_null()
            )
            .then(
                pl.lit(value).cast(
                    self.df[column].dtype,
                    strict=False,
                )
            )
            .otherwise(pl.col(column))
            .alias(column)
        )

        return self._log_column_changes(
            f"contextual_fill_{statistic}",
            column,
            before,
        )

    def handle_outliers(
        self,
        column: str,
        strategy: str,
        lower_bound: Optional[float] = None,
        upper_bound: Optional[float] = None,
    ) -> int:
        self._require_column(column)
        strategy = strategy.lower()

        if strategy == "keep":
            return 0

        if lower_bound is None or upper_bound is None:
            raise ValueError(
                "Outlier bounds are required for this strategy."
            )

        mask = (
            (pl.col(column) < lower_bound)
            | (pl.col(column) > upper_bound)
        ).fill_null(False)

        if strategy == "winsorize":
            before = self.df[column].clone()
            dtype = self.df[column].dtype

            self.df = self.df.with_columns(
                pl.when(pl.col(column) < lower_bound)
                .then(
                    pl.lit(lower_bound).cast(
                        dtype,
                        strict=False,
                    )
                )
                .when(pl.col(column) > upper_bound)
                .then(
                    pl.lit(upper_bound).cast(
                        dtype,
                        strict=False,
                    )
                )
                .otherwise(pl.col(column))
                .alias(column)
            )

            return self._log_column_changes(
                "winsorize_outliers",
                column,
                before,
            )

        if strategy == "drop":
            return self._remove_rows(self.df.select(mask).to_series(), "drop_outlier_rows", column)

        raise ValueError(
            f"Unsupported outlier strategy: {strategy}"
        )

    def coerce_numeric(self, column: str) -> int:
        self._require_column(column)
        if self.df[column].dtype != pl.Utf8:
            return 0

        before = self.df[column].clone()
        review = conversion_review(before, column, 'coerce_numeric')
        if review['blocked_count']:
            raise ValueError(f"Conversion blocked for {column}: {review['blocked_count']} values would be lost or reinterpreted. Keep this column as text.")
        self.df = self.df.with_columns(pl.col(column).str.strip_chars().cast(pl.Float64, strict=True))

        return self._log_column_changes(
            "coerce_numeric",
            column,
            before,
        )

    def parse_date(
        self,
        column: str,
        date_format: Optional[str] = None,
    ) -> int:
        self._require_column(column)
        if self.df[column].dtype != pl.Utf8:
            return 0

        before = self.df[column].clone()
        review = conversion_review(before, column, 'parse_date', date_format)
        if review['blocked_count']:
            raise ValueError(f"Conversion blocked for {column}: {review['blocked_count']} values cannot be safely parsed. Keep this column as text.")
        self.df = self.df.with_columns(
            pl.col(column)
            .str.strip_chars()
            .str.strptime(
                pl.Date,
                format=date_format,
                strict=False,
            )
            .alias(column)
        )

        return self._log_column_changes(
            "parse_date",
            column,
            before,
        )

    def drop_duplicates(
        self,
        subset: Optional[List[str]] = None,
    ) -> int:
        self._require_data()

        if subset:
            for column in subset:
                self._require_column(column)
        columns = subset or self.df.columns
        keep = self.df.select(pl.struct(columns).is_first_distinct()).to_series()
        return self._remove_rows(~keep, "drop_duplicates", "dataset" if subset is None else ",".join(subset))

    def _remove_rows(self, mask: pl.Series, action: str, column: str) -> int:
        self._require_data()
        mask = mask.fill_null(False)
        removed = int(mask.sum())
        if not removed:
            return 0
        self._operation += 1
        self.change_totals["removed"] += removed
        limit = removed if self.lineage_limit is None else max(0, self.lineage_limit - len(self.change_log))
        source_ids = self._source_rows.filter(mask).head(limit)
        snapshots = self.df.filter(mask).head(limit)
        for start in range(0, len(source_ids), 4096):
            records = []
            for row_id, snapshot in zip(source_ids.slice(start,4096), snapshots.slice(start,4096).iter_rows(named=True)):
                records.append({"action": action, "column": column, "row_index": str(row_id - 1),
                "source_row_id": row_id, "operation": self._operation, "change_kind": "removed",
                "old_value": json.dumps(snapshot, ensure_ascii=False, default=str, allow_nan=True),
                "new_value": "", "old_is_null": False, "new_is_null": True,
                    "method": action, "impact_count": 1})
            self._append_records(records)
        self.df = self.df.filter(~mask)
        self._source_rows = self._source_rows.filter(~mask)
        return removed

    def source_row_ids(self, limit=None):
        self._require_data()
        return (self._source_rows if limit is None else self._source_rows.head(limit)).to_list()

    def row_mapping(self):
        self._require_data()
        return pl.DataFrame({"output_row": pl.Series(range(1, self.df.height + 1), dtype=pl.UInt64),
                             "source_row_id": self._source_rows})

    def outcome_summary(self):
        self._require_data()
        changed_rows = {entry["source_row_id"] for entry in self.change_log if entry.get("change_kind") != "removed"}
        return {**self.change_totals, "retained_rows": self.df.height,
                "recorded_changed_rows": sum(self._changed_sources) if self.audit_path else len(changed_rows), "log_is_sample": self.lineage_limit is not None,
                "source_row_id_note": "1-based data record in the confirmed source snapshot; not a physical CSV line."}

    def save_cleaned_data(self, output_path: str) -> bool:
        self._require_data()

        try:
            write_tabular(self.df, output_path)
            return True
        except Exception as exc:
            print(f"Error saving cleaned file: {exc}")
            return False

    def get_change_log(self) -> List[Dict[str, Any]]:
        return list(self.change_log)

    def get_lineage_df(self) -> pl.DataFrame:
        if self.audit_path:
            return pl.read_csv(self.audit_path, schema=self.AUDIT_SCHEMA)
        schema = {"action": pl.String, "column": pl.String, "row_index": pl.String,
                  "source_row_id": pl.UInt64, "operation": pl.UInt32, "change_kind": pl.String,
                  "old_value": pl.String, "new_value": pl.String, "old_is_null": pl.Boolean,
                  "new_is_null": pl.Boolean, "method": pl.String, "impact_count": pl.Int64}
        return pl.DataFrame(self.change_log, schema=schema, strict=False)

    def get_lineage_summary(self) -> List[Dict[str, Any]]:
        if self.audit_path:
            return [dict(action=action,column=column,changes=count) for (action,column),count in self._audit_summary.items()]
        summary: Dict[tuple, Dict[str, Any]] = {}

        for entry in self.change_log:
            key = (
                entry["action"],
                entry["column"],
            )
            if key not in summary:
                summary[key] = {
                    "action": entry["action"],
                    "column": entry["column"],
                    "changes": 0,
                }

            summary[key]["changes"] += int(
                entry.get("impact_count", 1) or 1
            )

        return list(summary.values())
