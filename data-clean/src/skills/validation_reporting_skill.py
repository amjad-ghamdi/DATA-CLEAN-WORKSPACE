from typing import Any, Dict, List


class ValidationReportingSkill:
    """Compare before/after quality profiles and produce a concise validation summary."""

    @staticmethod
    def _total_missing(profile: Dict[str, Any]) -> int:
        return sum(
            int(item.get("count", 0) or 0)
            for item in profile.get("missing_values", {}).values()
            if isinstance(item, dict)
        )

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

        before_columns = list(before_profile.get("schema", {}).keys())
        after_columns = list(after_profile.get("schema", {}).keys())
        column_consistency = before_columns == after_columns

        return {
            "status": "PASS" if column_consistency else "WARNING",
            "rows_before": before_rows,
            "rows_after": after_rows,
            "rows_removed": rows_removed,
            "duplicates_before": int(before_duplicates.get("duplicate_rows", 0) or 0),
            "duplicates_after": int(after_duplicates.get("duplicate_rows", 0) or 0),
            "missing_before": self._total_missing(before_profile),
            "missing_after": self._total_missing(after_profile),
            "column_consistency": column_consistency,
            "actions_applied": len(applied_actions),
            "lineage_entries": lineage_entries,
        }
