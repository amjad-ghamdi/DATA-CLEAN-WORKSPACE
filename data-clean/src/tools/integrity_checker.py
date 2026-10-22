from pathlib import Path
from typing import Any, Dict, List, Optional

import polars as pl

from .tabular_io import read_tabular


class IntegrityChecker:
    """Protect identifier columns and flag integrity-sensitive operations."""

    def __init__(
        self,
        file_path: str,
        dataframe: Optional[pl.DataFrame] = None,
    ):
        self.file_path = Path(file_path)
        self.df = dataframe
        self.id_keywords = {
            "id",
            "uuid",
            "key",
            "code",
            "pk",
            "fk",
            "reference",
        }
        self._primary_keys_cache: Optional[List[str]] = None

    def load_data(self) -> bool:
        if self.df is not None:
            return True

        try:
            self.df = read_tabular(self.file_path)
            return True
        except Exception as exc:
            print(f"Error loading file for integrity check: {exc}")
            return False

    def detect_primary_keys(self) -> List[str]:
        if self.df is None:
            return []

        if self._primary_keys_cache is not None:
            return list(self._primary_keys_cache)

        candidates = [
            column
            for column in self.df.columns
            if any(
                keyword in column.lower()
                for keyword in self.id_keywords
            )
        ]

        if not candidates:
            self._primary_keys_cache = []
            return []

        expressions = []
        for column in candidates:
            expressions.extend(
                [
                    pl.col(column)
                    .null_count()
                    .alias(f"null::{column}"),
                    pl.col(column)
                    .n_unique()
                    .alias(f"unique::{column}"),
                ]
            )

        metrics = self.df.select(expressions).to_dicts()[0]
        potential_keys = []

        for column in candidates:
            null_free = int(metrics[f"null::{column}"]) == 0
            is_unique = (
                int(metrics[f"unique::{column}"])
                == self.df.height
            )
            if null_free and is_unique:
                potential_keys.append(column)

        self._primary_keys_cache = potential_keys
        return list(potential_keys)

    def check_critical_impact(self, column: str, action: str) -> bool:
        if self.df is None:
            return False
        if column in self.detect_primary_keys():
            return True
        return action in {"drop_rows", "drop_missing_rows"}

    def verify_relationship_stability(
        self,
        column: str,
        old_values: List[Any],
        new_values: List[Any],
    ) -> bool:
        if self.df is None:
            return True
        if column not in self.df.columns:
            return False

        was_unique = (
            self.df[column].n_unique()
            == self.df.height
        )
        if was_unique and len(set(new_values)) != len(new_values):
            return False
        return True

    def get_integrity_report(self) -> Dict[str, Any]:
        if not self.load_data():
            return {"error": "Data not loaded"}

        keys = self.detect_primary_keys()
        return {
            "detected_primary_keys": keys,
            "protected_columns": keys,
            "total_columns": len(self.df.columns),
            "integrity_status": (
                "Stable"
                if keys
                else "Warning: No unique identifiers found"
            ),
            "critical_columns_count": len(keys),
        }
