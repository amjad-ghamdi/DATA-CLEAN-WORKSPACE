from pathlib import Path
from typing import Any, Dict, List

import polars as pl

from .tabular_io import read_tabular


class IntegrityChecker:
    """Protect identifier columns and flag operations that may alter dataset integrity."""

    def __init__(self, file_path: str):
        self.file_path = Path(file_path)
        self.df = None
        self.id_keywords = {"id", "uuid", "key", "code", "pk", "fk", "reference"}

    def load_data(self) -> bool:
        try:
            self.df = read_tabular(self.file_path)
            return True
        except Exception as exc:
            print(f"Error loading file for integrity check: {exc}")
            return False

    def detect_primary_keys(self) -> List[str]:
        if self.df is None:
            return []

        potential_keys = []
        for column in self.df.columns:
            column_lower = column.lower()
            name_match = any(keyword in column_lower for keyword in self.id_keywords)
            null_free = self.df[column].null_count() == 0
            is_unique = self.df[column].n_unique() == self.df.height
            if name_match and null_free and is_unique:
                potential_keys.append(column)
        return potential_keys

    def check_critical_impact(self, column: str, action: str) -> bool:
        if self.df is None:
            return False
        if column in self.detect_primary_keys():
            return True
        return action in {"drop_rows", "drop_missing_rows"}

    def verify_relationship_stability(
        self, column: str, old_values: List[Any], new_values: List[Any]
    ) -> bool:
        if self.df is None:
            return True
        if column not in self.df.columns:
            return False

        was_unique = self.df[column].n_unique() == self.df.height
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
            "integrity_status": "Stable" if keys else "Warning: No unique identifiers found",
            "critical_columns_count": len(keys),
        }
