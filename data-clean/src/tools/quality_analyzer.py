from pathlib import Path
from typing import Any, Dict, List

import polars as pl

from .tabular_io import read_tabular


class QualityAnalyzer:
    """Profile quality, privacy, transformations, and outliers without changing data."""

    def __init__(self, file_path: str):
        self.file_path = Path(file_path)
        self.df = None
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

    def load_data(self) -> bool:
        try:
            self.df = read_tabular(self.file_path)
            return True
        except Exception as exc:
            print(f"Error loading file: {exc}")
            return False

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

        total_rows = self.df.height
        null_counts = self.df.null_count().row(0)
        report = {}
        for index, column in enumerate(self.df.columns):
            count = int(null_counts[index])
            percentage_value = (count / total_rows * 100) if total_rows else 0.0
            report[column] = {
                "count": count,
                "percentage": f"{percentage_value:.2f}%",
                "percentage_value": round(percentage_value, 4),
            }
        return report

    def analyze_inconsistencies(self) -> Dict[str, Any]:
        if self.df is None:
            return {"error": "Data not loaded"}

        inconsistencies = {}
        for column in self.df.columns:
            if self.df[column].dtype != pl.Utf8:
                continue

            unique_count = self.df[column].n_unique()
            # Avoid treating free-text/high-cardinality columns like titles as categories.
            if unique_count > min(250, max(30, int(self.df.height * 0.35))):
                continue

            unique_values = self.df[column].unique().to_list()
            variants: Dict[str, List[Any]] = {}
            for value in unique_values:
                if value is None:
                    continue
                normalized = str(value).lower().strip()
                variants.setdefault(normalized, []).append(value)

            groups = {key: values for key, values in variants.items() if len(values) > 1}
            if groups:
                inconsistencies[column] = groups

        return inconsistencies

    def analyze_duplicates(self) -> Dict[str, Any]:
        if self.df is None:
            return {"error": "Data not loaded"}

        total_rows = self.df.height
        unique_rows = self.df.unique(maintain_order=True).height
        duplicate_rows = total_rows - unique_rows
        percentage = (duplicate_rows / total_rows * 100) if total_rows else 0.0
        return {
            "total_rows": total_rows,
            "unique_rows": unique_rows,
            "duplicate_rows": duplicate_rows,
            "percentage": f"{percentage:.2f}%",
            "percentage_value": round(percentage, 4),
        }

    def get_schema_info(self) -> Dict[str, str]:
        if self.df is None:
            return {"error": "Data not loaded"}
        return {column: str(dtype) for column, dtype in self.df.schema.items()}

    def get_column_profiles(self) -> Dict[str, Any]:
        if self.df is None:
            return {"error": "Data not loaded"}

        profiles = {}
        total_rows = self.df.height
        missing = self.analyze_missing_values()
        for column in self.df.columns:
            profiles[column] = {
                "dtype": str(self.df[column].dtype),
                "unique_count": int(self.df[column].n_unique()),
                "unique_ratio": round(
                    self.df[column].n_unique() / total_rows, 4
                ) if total_rows else 0.0,
                "missing_count": missing[column]["count"],
                "missing_percentage": missing[column]["percentage_value"],
            }
        return profiles

    def detect_transformation_candidates(self) -> List[Dict[str, Any]]:
        """Suggest conservative type transformations for string columns."""
        if self.df is None:
            return []

        candidates: List[Dict[str, Any]] = []
        for column in self.df.columns:
            if self.df[column].dtype != pl.Utf8:
                continue

            series = self.df[column].drop_nulls()
            if series.len() == 0:
                continue

            text = series.cast(pl.Utf8).str.strip_chars()
            non_empty = text.filter(text != "")
            if non_empty.len() == 0:
                continue

            # Numeric candidate: commas are allowed, but no aggressive symbol stripping.
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
                        "reason": f"{numeric_ratio:.0%} of non-null values parse as numbers.",
                    }
                )
                continue

            column_lower = column.lower()
            date_hint = any(
                token in column_lower
                for token in ("date", "time", "created", "updated", "released", "added")
            )
            if date_hint:
                try:
                    parsed_dates = non_empty.str.strptime(pl.Date, strict=False)
                    date_success = int(parsed_dates.is_not_null().sum())
                    date_ratio = date_success / non_empty.len()
                    if date_ratio >= 0.80:
                        candidates.append(
                            {
                                "column": column,
                                "action": "parse_date",
                                "confidence": round(date_ratio, 4),
                                "target_type": "Date",
                                "reason": f"{date_ratio:.0%} of non-null values parse as dates.",
                            }
                        )
                except Exception:
                    pass

        return candidates

    def analyze_outliers(self) -> Dict[str, Any]:
        """Detect numeric outliers using IQR; never changes them automatically."""
        if self.df is None:
            return {"error": "Data not loaded"}

        report = {}
        for column in self.df.columns:
            dtype_name = str(self.df[column].dtype).lower()
            if not any(token in dtype_name for token in ("int", "float", "decimal")):
                continue

            series = self.df[column].drop_nulls()
            if series.len() < 4:
                continue
            try:
                q1 = series.quantile(0.25)
                q3 = series.quantile(0.75)
                if q1 is None or q3 is None:
                    continue
                iqr = q3 - q1
                lower = q1 - 1.5 * iqr
                upper = q3 + 1.5 * iqr
                count = int(((series < lower) | (series > upper)).sum())
                report[column] = {
                    "count": count,
                    "lower_bound": float(lower),
                    "upper_bound": float(upper),
                }
            except Exception:
                continue
        return report

    def run_full_profile(self) -> Dict[str, Any]:
        if not self.load_data():
            return {"error": "Could not load data for profiling"}

        return {
            "privacy": self.analyze_privacy(),
            "missing_values": self.analyze_missing_values(),
            "inconsistencies": self.analyze_inconsistencies(),
            "duplicates": self.analyze_duplicates(),
            "schema": self.get_schema_info(),
            "column_profiles": self.get_column_profiles(),
            "transformation_candidates": self.detect_transformation_candidates(),
            "outliers": self.analyze_outliers(),
        }
