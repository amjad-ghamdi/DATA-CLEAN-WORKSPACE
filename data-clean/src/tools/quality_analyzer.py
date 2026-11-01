from pathlib import Path
from typing import Any, Dict, List, Optional

import polars as pl

from .tabular_io import LARGE_DATASET_THRESHOLD, read_tabular


class QualityAnalyzer:
    """Profile quality with a faster path for large datasets."""

    DEFAULT_SAMPLE_SIZE = 50_000

    def __init__(
        self,
        file_path: str,
        dataframe: Optional[pl.DataFrame] = None,
        large_dataset_threshold: int = LARGE_DATASET_THRESHOLD,
        sample_size: int = DEFAULT_SAMPLE_SIZE,
    ):
        self.file_path = Path(file_path)
        self.df = dataframe
        self.large_dataset_threshold = large_dataset_threshold
        self.sample_size = sample_size
        self.sensitive_keywords = {
            "email",
            "phone",
            "address",
            "password",
            "ssn",
            "credit_card",
            "name",
            "birthdate",
            "salary",
            "iban",
            "national_id",
        }
        self.id_keywords = {"id", "uuid", "key", "code", "pk", "fk", "reference"}
        self._missing_cache: Optional[Dict[str, Any]] = None
        self._column_profile_cache: Optional[Dict[str, Any]] = None
        self._analysis_df: Optional[pl.DataFrame] = None

    def load_data(self) -> bool:
        if self.df is not None:
            return True
        try:
            self.df = read_tabular(self.file_path)
            return True
        except Exception as exc:
            print(f"Error loading file: {exc}")
            return False

    @property
    def is_large_dataset(self) -> bool:
        return self.df is not None and self.df.height >= self.large_dataset_threshold

    def _get_analysis_df(self) -> pl.DataFrame:
        if self.df is None:
            raise ValueError("Data not loaded")
        if self._analysis_df is not None:
            return self._analysis_df

        if self.is_large_dataset and self.df.height > self.sample_size:
            self._analysis_df = self.df.sample(
                n=self.sample_size,
                seed=42,
                shuffle=True,
            )
        else:
            self._analysis_df = self.df
        return self._analysis_df

    def get_processing_metadata(self) -> Dict[str, Any]:
        if self.df is None:
            return {"mode": "unknown"}

        analysis_rows = self._get_analysis_df().height
        return {
            "mode": "large_dataset" if self.is_large_dataset else "standard",
            "rows": self.df.height,
            "sample_rows": analysis_rows,
            "expensive_checks_sampled": self.is_large_dataset,
            "large_dataset_threshold": self.large_dataset_threshold,
            "engine": "polars_parallel_columnar",
        }

    def analyze_privacy(self) -> Dict[str, Any]:
        if self.df is None:
            return {"error": "Data not loaded"}

        sensitive_columns = []
        for column in self.df.columns:
            column_lower = column.lower()
            if any(keyword in column_lower for keyword in self.sensitive_keywords):
                sensitive_columns.append(column)

        return {
            "sensitive_columns": sensitive_columns,
            "privacy_risk": "High" if sensitive_columns else "Low",
            "count": len(sensitive_columns),
        }

    def analyze_missing_values(self) -> Dict[str, Any]:
        if self.df is None:
            return {"error": "Data not loaded"}
        if self._missing_cache is not None:
            return self._missing_cache

        total_rows = self.df.height
        null_counts = self.df.null_count().row(0)
        report: Dict[str, Any] = {}

        for index, column in enumerate(self.df.columns):
            count = int(null_counts[index])
            percentage_value = (count / total_rows * 100) if total_rows else 0.0
            report[column] = {
                "count": count,
                "percentage": f"{percentage_value:.2f}%",
                "percentage_value": round(percentage_value, 4),
            }

        self._missing_cache = report
        return report

    @staticmethod
    def _n_unique_for_frame(frame: pl.DataFrame) -> Dict[str, int]:
        if not frame.columns:
            return {}

        values = frame.select(
            [pl.col(column).n_unique().alias(column) for column in frame.columns]
        ).row(0)

        return {
            column: int(values[index])
            for index, column in enumerate(frame.columns)
        }

    def analyze_inconsistencies(self) -> Dict[str, Any]:
        if self.df is None:
            return {"error": "Data not loaded"}

        frame = self._get_analysis_df()
        inconsistencies: Dict[str, Any] = {}

        for column in frame.columns:
            if frame[column].dtype != pl.Utf8:
                continue

            unique_count = frame[column].n_unique()
            if unique_count > min(250, max(30, int(frame.height * 0.35))):
                continue

            unique_values = frame[column].unique().to_list()
            variants: Dict[str, List[Any]] = {}
            for value in unique_values:
                if value is None:
                    continue
                normalized = str(value).lower().strip()
                variants.setdefault(normalized, []).append(value)

            groups = {
                key: values
                for key, values in variants.items()
                if len(values) > 1
            }
            if groups:
                inconsistencies[column] = groups

        return inconsistencies

    def analyze_duplicates(self) -> Dict[str, Any]:
        if self.df is None:
            return {"error": "Data not loaded"}

        total_rows = self.df.height
        unique_rows = int(self.df.hash_rows().n_unique()) if total_rows else 0
        duplicate_rows = total_rows - unique_rows
        percentage = (duplicate_rows / total_rows * 100) if total_rows else 0.0

        return {
            "total_rows": total_rows,
            "unique_rows": unique_rows,
            "duplicate_rows": duplicate_rows,
            "percentage": f"{percentage:.2f}%",
            "percentage_value": round(percentage, 4),
            "method": "row_hash",
        }

    def get_schema_info(self) -> Dict[str, str]:
        if self.df is None:
            return {"error": "Data not loaded"}
        return {
            column: str(dtype)
            for column, dtype in self.df.schema.items()
        }

    def get_column_profiles(self) -> Dict[str, Any]:
        if self.df is None:
            return {"error": "Data not loaded"}
        if self._column_profile_cache is not None:
            return self._column_profile_cache

        frame = self._get_analysis_df()
        missing = self.analyze_missing_values()
        unique_counts = self._n_unique_for_frame(frame)
        denominator = frame.height
        profiles: Dict[str, Any] = {}

        for column in self.df.columns:
            unique_count = int(unique_counts.get(column, 0))
            profiles[column] = {
                "dtype": str(self.df[column].dtype),
                "unique_count": unique_count,
                "unique_ratio": round(unique_count / denominator, 4)
                if denominator
                else 0.0,
                "unique_count_estimated": self.is_large_dataset,
                "missing_count": missing[column]["count"],
                "missing_percentage": missing[column]["percentage_value"],
            }

        self._column_profile_cache = profiles
        return profiles

    def detect_transformation_candidates(self) -> List[Dict[str, Any]]:
        """Suggest conservative type transformations for string columns."""
        if self.df is None:
            return []

        frame = self._get_analysis_df()
        candidates: List[Dict[str, Any]] = []

        for column in frame.columns:
            if frame[column].dtype != pl.Utf8:
                continue

            series = frame[column].drop_nulls()
            if series.len() == 0:
                continue

            text = series.cast(pl.Utf8).str.strip_chars()
            non_empty = text.filter(text != "")
            if non_empty.len() == 0:
                continue

            numeric_parsed = non_empty.str.replace_all(",", "").cast(
                pl.Float64, strict=False
            )
            numeric_success = int(numeric_parsed.is_not_null().sum())
            numeric_ratio = numeric_success / non_empty.len()

            if numeric_ratio >= 0.95:
                candidates.append(
                    {
                        "column": column,
                        "action": "coerce_numeric",
                        "confidence": round(numeric_ratio, 4),
                        "target_type": "Float64",
                        "reason": (
                            f"{numeric_ratio:.0%} of checked non-null values "
                            "parse as numbers."
                        ),
                        "sampled": self.is_large_dataset,
                    }
                )
                continue

            column_lower = column.lower()
            date_hint = any(
                token in column_lower
                for token in (
                    "date",
                    "time",
                    "created",
                    "updated",
                    "released",
                    "added",
                )
            )

            if date_hint:
                try:
                    parsed_dates = non_empty.str.strptime(
                        pl.Date,
                        strict=False,
                    )
                    date_success = int(parsed_dates.is_not_null().sum())
                    date_ratio = date_success / non_empty.len()

                    if date_ratio >= 0.80:
                        candidates.append(
                            {
                                "column": column,
                                "action": "parse_date",
                                "confidence": round(date_ratio, 4),
                                "target_type": "Date",
                                "reason": (
                                    f"{date_ratio:.0%} of checked non-null values "
                                    "parse as dates."
                                ),
                                "sampled": self.is_large_dataset,
                            }
                        )
                except Exception:
                    pass

        return candidates

    def analyze_outliers(self) -> Dict[str, Any]:
        """Use sampled IQR bounds for large data, then count matches on all rows."""
        if self.df is None:
            return {"error": "Data not loaded"}

        frame = self._get_analysis_df()
        bounds: Dict[str, tuple[float, float]] = {}

        for column in frame.columns:
            dtype_name = str(frame[column].dtype).lower()
            if not any(
                token in dtype_name
                for token in ("int", "float", "decimal")
            ):
                continue

            series = frame[column].drop_nulls()
            if series.len() < 4:
                continue

            try:
                q1 = series.quantile(0.25)
                q3 = series.quantile(0.75)
                if q1 is None or q3 is None:
                    continue

                iqr = q3 - q1
                bounds[column] = (
                    float(q1 - 1.5 * iqr),
                    float(q3 + 1.5 * iqr),
                )
            except Exception:
                continue

        if not bounds:
            return {}

        count_exprs = [
            (
                (
                    (pl.col(column) < lower)
                    | (pl.col(column) > upper)
                )
                .fill_null(False)
                .sum()
                .alias(column)
            )
            for column, (lower, upper) in bounds.items()
        ]
        counts = self.df.select(count_exprs).row(0)

        report: Dict[str, Any] = {}
        for index, (column, (lower, upper)) in enumerate(bounds.items()):
            report[column] = {
                "count": int(counts[index]),
                "lower_bound": lower,
                "upper_bound": upper,
                "bounds_estimated": self.is_large_dataset,
                "sample_size": frame.height,
            }

        return report

    def run_full_profile(self) -> Dict[str, Any]:
        if not self.load_data():
            return {"error": "Could not load data for profiling"}

        missing = self.analyze_missing_values()
        column_profiles = self.get_column_profiles()

        return {
            "processing": self.get_processing_metadata(),
            "privacy": self.analyze_privacy(),
            "missing_values": missing,
            "inconsistencies": self.analyze_inconsistencies(),
            "duplicates": self.analyze_duplicates(),
            "schema": self.get_schema_info(),
            "column_profiles": column_profiles,
            "transformation_candidates": self.detect_transformation_candidates(),
            "outliers": self.analyze_outliers(),
        }
