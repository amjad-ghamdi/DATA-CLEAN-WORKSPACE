from typing import Any, Dict, List


class ValidationReportingSkill:
    """Compare before/after profiles and flag structural or type regressions."""

    @staticmethod
    def _total_missing(profile: Dict[str, Any]) -> int:
        return sum(
            int(item.get("count", 0) or 0)
            for item in profile.get("missing_values", {}).values()
            if isinstance(item, dict)
        )

    @staticmethod
    def _is_numeric(dtype: str) -> bool:
        normalized = str(dtype).lower()
        return any(token in normalized for token in ("int", "float", "decimal"))

    @staticmethod
    def _is_string(dtype: str) -> bool:
        normalized = str(dtype).lower()
        return "string" in normalized or "utf8" in normalized

    def validate(
        self,
        before_profile: Dict[str, Any],
        after_profile: Dict[str, Any],
        applied_actions: List[Dict[str, Any]],
        lineage_entries: int,
    ) -> Dict[str, Any]:
        before_duplicates = before_profile.get("duplicates", {})
        after_duplicates = after_profile.get("duplicates", {})

        before_rows = int(before_duplicates.get("total_rows", 0) or 0)
        after_rows = int(after_duplicates.get("total_rows", 0) or 0)
        rows_removed = before_rows - after_rows

        before_schema = before_profile.get("schema", {})
        after_schema = after_profile.get("schema", {})
        before_columns = list(before_schema.keys())
        after_columns = list(after_schema.keys())
        column_consistency = before_columns == after_columns

        schema_changes = {
            column: {"before": before_schema.get(column), "after": after_schema.get(column)}
            for column in before_columns
            if before_schema.get(column) != after_schema.get(column)
        }

        warnings: List[str] = []
        for column, change in schema_changes.items():
            if self._is_numeric(change["before"]) and self._is_string(change["after"]):
                warnings.append(
                    f"{column} changed from numeric to text; review the selected fill or transformation strategy."
                )

        duplicates_before = int(before_duplicates.get("duplicate_rows", 0) or 0)
        duplicates_after = int(after_duplicates.get("duplicate_rows", 0) or 0)
        if duplicates_after > duplicates_before:
            warnings.append("Duplicate rows increased after cleaning.")

        missing_before = self._total_missing(before_profile)
        missing_after = self._total_missing(after_profile)

        status = "PASS" if column_consistency and not warnings else "WARNING"

        return {
            "status": status,
            "rows_before": before_rows,
            "rows_after": after_rows,
            "rows_removed": rows_removed,
            "duplicates_before": duplicates_before,
            "duplicates_after": duplicates_after,
            "missing_before": missing_before,
            "missing_after": missing_after,
            "column_consistency": column_consistency,
            "schema_changes": schema_changes,
            "warnings": warnings,
            "actions_applied": len(applied_actions),
            "lineage_entries": lineage_entries,
        }
