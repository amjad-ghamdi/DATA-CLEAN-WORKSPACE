from pathlib import Path
from typing import Any, Dict, List, Optional

import polars as pl

from .tabular_io import read_tabular, write_tabular


class CleaningEngine:
    """Execute deterministic cleaning actions with vectorized lineage tracking."""

    NULL_TOKENS = ["", "na", "n/a", "null", "none"]

    def __init__(self, file_path: str):
        self.file_path = Path(file_path)
        self.df: Optional[pl.DataFrame] = None
        self.change_log: List[Dict[str, Any]] = []
        self.lineage_limit = None

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
                }
            )
            .with_row_index("row_index")
            .filter(~old_value.eq_missing(new_value))
        )

        changed = changes.height
        if changed == 0:
            return 0

        if self.lineage_limit is not None:
            changes = changes.head(max(0, self.lineage_limit - len(self.change_log)))
        records = changes.select(
            [
                pl.lit(action).alias("action"),
                pl.lit(column).alias("column"),
                pl.col("row_index").cast(pl.Utf8),
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
        ).to_dicts()

        self.change_log.extend(records)
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
            initial_count = self.df.height
            self.df = self.df.drop_nulls(subset=[column])
            removed = initial_count - self.df.height

            if removed:
                self._log_change(
                    "drop_missing_rows",
                    column,
                    "dataset",
                    f"{initial_count} rows",
                    f"{self.df.height} rows",
                    impact_count=removed,
                )
            return removed

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
            initial_count = self.df.height
            self.df = self.df.filter(~mask)
            removed = initial_count - self.df.height

            if removed:
                self._log_change(
                    "drop_outlier_rows",
                    column,
                    "dataset",
                    f"{initial_count} rows",
                    f"{self.df.height} rows",
                    impact_count=removed,
                )
            return removed

        raise ValueError(
            f"Unsupported outlier strategy: {strategy}"
        )

    def coerce_numeric(self, column: str) -> int:
        self._require_column(column)
        if self.df[column].dtype != pl.Utf8:
            return 0

        before = self.df[column].clone()
        self.df = self.df.with_columns(
            pl.col(column)
            .str.strip_chars()
            .str.replace_all(",", "")
            .cast(pl.Float64, strict=False)
            .alias(column)
        )

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

        initial_count = self.df.height
        self.df = self.df.unique(
            subset=subset,
            maintain_order=True,
        )
        final_count = self.df.height
        removed = initial_count - final_count

        if removed:
            self._log_change(
                "drop_duplicates",
                (
                    "dataset"
                    if subset is None
                    else ",".join(subset)
                ),
                "dataset",
                f"{initial_count} rows",
                f"{final_count} rows",
                impact_count=removed,
            )

        return removed

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
        if not self.change_log:
            return pl.DataFrame(
                schema={
                    "action": pl.Utf8,
                    "column": pl.Utf8,
                    "row_index": pl.Utf8,
                    "old_value": pl.Utf8,
                    "new_value": pl.Utf8,
                    "impact_count": pl.Int64,
                }
            )

        return pl.DataFrame(
            self.change_log,
            schema_overrides={
                "action": pl.Utf8,
                "column": pl.Utf8,
                "row_index": pl.Utf8,
                "old_value": pl.Utf8,
                "new_value": pl.Utf8,
                "impact_count": pl.Int64,
            },
            strict=False,
        )

    def get_lineage_summary(self) -> List[Dict[str, Any]]:
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
