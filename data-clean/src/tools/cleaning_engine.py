from pathlib import Path
from typing import Any, Dict, List, Optional

import polars as pl


class CleaningEngine:
    """Execute deterministic cleaning/transformation actions with lineage tracking."""

    NULL_TOKENS = ["", "na", "n/a", "null", "none"]

    def __init__(self, file_path: str):
        self.file_path = Path(file_path)
        self.df: Optional[pl.DataFrame] = None
        self.change_log: List[Dict[str, Any]] = []

    def load_data(self) -> bool:
        try:
            if self.file_path.suffix.lower() != ".csv":
                return False
            self.df = pl.read_csv(self.file_path)
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
        if old_val == new_val:
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

    def _log_series_changes(
        self,
        action: str,
        column: str,
        old_values: List[Any],
        new_values: List[Any],
    ) -> int:
        changed = 0
        for index, (old, new) in enumerate(zip(old_values, new_values)):
            if old != new:
                self._log_change(action, column, index, old, new)
                changed += 1
        return changed

    def trim_whitespace(self, columns: List[str]) -> int:
        """Remove leading/trailing whitespace from string columns."""
        self._require_data()
        total_changed = 0
        for column in columns:
            self._require_column(column)
            if self.df[column].dtype != pl.Utf8:
                continue
            before = self.df[column].to_list()
            self.df = self.df.with_columns(pl.col(column).str.strip_chars())
            after = self.df[column].to_list()
            total_changed += self._log_series_changes(
                "trim_whitespace", column, before, after
            )
        return total_changed

    def standardize_null_tokens(
        self, column: str, tokens: Optional[List[str]] = None
    ) -> int:
        """Convert common textual null markers to actual null values."""
        self._require_column(column)
        if self.df[column].dtype != pl.Utf8:
            return 0

        normalized_tokens = [token.lower() for token in (tokens or self.NULL_TOKENS)]
        before = self.df[column].to_list()
        self.df = self.df.with_columns(
            pl.when(pl.col(column).str.strip_chars().str.to_lowercase().is_in(normalized_tokens))
            .then(None)
            .otherwise(pl.col(column))
            .alias(column)
        )
        after = self.df[column].to_list()
        return self._log_series_changes(
            "standardize_null_tokens", column, before, after
        )

    def normalize_casing(self, column: str, target_case: str = "lower") -> int:
        """Normalize text casing for reviewed categorical columns."""
        self._require_column(column)
        if self.df[column].dtype != pl.Utf8:
            return 0

        before = self.df[column].to_list()
        if target_case == "lower":
            expression = pl.col(column).str.to_lowercase()
        elif target_case == "upper":
            expression = pl.col(column).str.to_uppercase()
        elif target_case == "title":
            expression = pl.col(column).str.to_titlecase()
        else:
            raise ValueError(f"Unsupported target_case: {target_case}")

        self.df = self.df.with_columns(expression.alias(column))
        after = self.df[column].to_list()
        return self._log_series_changes("normalize_casing", column, before, after)

    def fill_nulls(
        self,
        column: str,
        strategy: str,
        fill_value: Any = None,
    ) -> int:
        """Handle missing values using a user-approved strategy."""
        self._require_column(column)
        strategy = strategy.lower()
        if strategy == "keep":
            return 0

        before = self.df[column].to_list()

        if strategy == "constant":
            if fill_value is None:
                raise ValueError("fill_value is required for constant imputation.")
            self.df = self.df.with_columns(pl.col(column).fill_null(fill_value))

        elif strategy == "mean":
            mean_value = self.df[column].mean()
            if mean_value is None:
                return 0
            self.df = self.df.with_columns(pl.col(column).fill_null(mean_value))

        elif strategy == "median":
            median_value = self.df[column].median()
            if median_value is None:
                return 0
            self.df = self.df.with_columns(pl.col(column).fill_null(median_value))

        elif strategy == "mode":
            mode_result = self.df[column].drop_nulls().mode()
            if mode_result.len() == 0:
                return 0
            mode_value = mode_result.to_list()[0]
            self.df = self.df.with_columns(pl.col(column).fill_null(mode_value))

        elif strategy == "drop":
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

        else:
            raise ValueError(f"Unsupported missing-value strategy: {strategy}")

        after = self.df[column].to_list()
        return self._log_series_changes(
            f"fill_nulls_{strategy}", column, before, after
        )

    def coerce_numeric(self, column: str) -> int:
        """Convert reviewed numeric-looking text to Float64."""
        self._require_column(column)
        if self.df[column].dtype != pl.Utf8:
            return 0

        before = self.df[column].to_list()
        self.df = self.df.with_columns(
            pl.col(column)
            .str.strip_chars()
            .str.replace_all(",", "")
            .cast(pl.Float64, strict=False)
            .alias(column)
        )
        after = self.df[column].to_list()
        return self._log_series_changes("coerce_numeric", column, before, after)

    def parse_date(self, column: str, date_format: Optional[str] = None) -> int:
        """Parse reviewed date-like text into a Date column."""
        self._require_column(column)
        if self.df[column].dtype != pl.Utf8:
            return 0

        before = self.df[column].to_list()
        expression = pl.col(column).str.strip_chars().str.strptime(
            pl.Date,
            format=date_format,
            strict=False,
        )
        self.df = self.df.with_columns(expression.alias(column))
        after = self.df[column].to_list()
        return self._log_series_changes("parse_date", column, before, after)

    def drop_duplicates(self, subset: Optional[List[str]] = None) -> int:
        """Remove exact or subset duplicates while preserving row order."""
        self._require_data()
        initial_count = self.df.height
        self.df = self.df.unique(subset=subset, maintain_order=True)
        final_count = self.df.height
        removed = initial_count - final_count
        if removed:
            self._log_change(
                "drop_duplicates",
                "dataset" if subset is None else ",".join(subset),
                "dataset",
                f"{initial_count} rows",
                f"{final_count} rows",
                impact_count=removed,
            )
        return removed

    def save_cleaned_data(self, output_path: str) -> bool:
        self._require_data()
        try:
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            self.df.write_csv(output_path)
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

        # CSV lineage should remain stable even when source columns contain mixed types.
        records = []
        for entry in self.change_log:
            records.append(
                {
                    "action": str(entry.get("action", "")),
                    "column": str(entry.get("column", "")),
                    "row_index": str(entry.get("row_index", "")),
                    "old_value": "" if entry.get("old_value") is None else str(entry.get("old_value")),
                    "new_value": "" if entry.get("new_value") is None else str(entry.get("new_value")),
                    "impact_count": int(entry.get("impact_count", 1) or 1),
                }
            )
        return pl.DataFrame(records)

    def get_lineage_summary(self) -> List[Dict[str, Any]]:
        summary: Dict[tuple, Dict[str, Any]] = {}
        for entry in self.change_log:
            key = (entry["action"], entry["column"])
            if key not in summary:
                summary[key] = {
                    "action": entry["action"],
                    "column": entry["column"],
                    "changes": 0,
                }
            summary[key]["changes"] += int(entry.get("impact_count", 1) or 1)
        return list(summary.values())
