from typing import Any, Dict, List, Optional


class SafeCleaningSkill:
    """Build a risk-aware cleaning and transformation plan from a quality profile."""

    NUMERIC_MARKERS = ("int", "float", "decimal")

    def __init__(self):
        self.cleaning_plan: List[Dict[str, Any]] = []

    @staticmethod
    def _is_numeric(dtype: str) -> bool:
        normalized = dtype.lower()
        return any(marker in normalized for marker in SafeCleaningSkill.NUMERIC_MARKERS)

    @staticmethod
    def _is_string(dtype: str) -> bool:
        normalized = dtype.lower()
        return "utf8" in normalized or "string" in normalized

    def build_cleaning_plan(
        self,
        quality_report: Dict[str, Any],
        integrity_report: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        integrity_report = integrity_report or {}
        protected_columns = set(integrity_report.get("protected_columns", []))
        schema = quality_report.get("schema", {})
        column_profiles = quality_report.get("column_profiles", {})
        plan: List[Dict[str, Any]] = []
        step = 1

        def add(item: Dict[str, Any]) -> None:
            nonlocal step
            item["id"] = f"step-{step}"
            item.setdefault("enabled_by_default", item.get("risk") == "SAFE")
            item.setdefault("executable", True)
            plan.append(item)
            step += 1

        # Reversible text cleanup can run automatically.
        for column, dtype in schema.items():
            if self._is_string(dtype):
                add(
                    {
                        "action": "trim_whitespace",
                        "column": column,
                        "risk": "SAFE",
                        "description": "Trim leading and trailing whitespace",
                        "reason": "Whitespace trimming is reversible and does not change intended values.",
                    }
                )

        # Casing normalization requires user review and is limited to detected variants.
        for column, variants in quality_report.get("inconsistencies", {}).items():
            if column in protected_columns:
                risk = "HIGH IMPACT"
            else:
                risk = "REVIEW"
            add(
                {
                    "action": "normalize_casing",
                    "column": column,
                    "risk": risk,
                    "description": "Normalize inconsistent category casing",
                    "reason": f"Detected {len(variants)} casing/spacing variant group(s).",
                    "target_case": "title",
                    "enabled_by_default": False,
                }
            )

        # Missing value strategy is type- and percentage-aware.
        missing_report = quality_report.get("missing_values", {})
        for column, info in missing_report.items():
            count = int(info.get("count", 0) or 0)
            if count == 0:
                continue

            percentage = float(info.get("percentage_value", 0.0) or 0.0)
            dtype = schema.get(column, "")
            profile = column_profiles.get(column, {})
            unique_ratio = float(profile.get("unique_ratio", 1.0) or 1.0)

            if column in protected_columns:
                recommended_strategy = "keep"
                risk = "HIGH IMPACT"
                reason = "Missing values affect a protected identifier column; automatic imputation is blocked."
            elif self._is_numeric(dtype) and percentage <= 20:
                recommended_strategy = "median"
                risk = "REVIEW"
                reason = "Low-to-moderate numeric missingness; median is robust to outliers but requires approval."
            elif self._is_string(dtype) and percentage <= 10 and unique_ratio <= 0.20:
                recommended_strategy = "mode"
                risk = "REVIEW"
                reason = "Low categorical missingness; mode imputation is available after review."
            else:
                recommended_strategy = "keep"
                risk = "REVIEW"
                reason = "Missingness is substantial or semantically ambiguous; preserving nulls is safest by default."

            options = ["keep", "median", "mean", "constant", "drop"] if self._is_numeric(dtype) else ["keep", "mode", "constant", "drop"]
            add(
                {
                    "action": "fill_nulls",
                    "column": column,
                    "risk": risk,
                    "description": f"Handle {count} missing value(s)",
                    "reason": reason,
                    "strategy": recommended_strategy,
                    "strategy_options": options,
                    "fill_value": None,
                    "enabled_by_default": False,
                }
            )

        # Type transformations are never automatic because they change representation.
        for candidate in quality_report.get("transformation_candidates", []):
            column = candidate["column"]
            risk = "HIGH IMPACT" if column in protected_columns else "REVIEW"
            add(
                {
                    "action": candidate["action"],
                    "column": column,
                    "risk": risk,
                    "description": f"Convert to {candidate['target_type']}",
                    "reason": candidate["reason"],
                    "confidence": candidate.get("confidence"),
                    "enabled_by_default": False,
                }
            )

        duplicates = quality_report.get("duplicates", {})
        duplicate_count = int(duplicates.get("duplicate_rows", 0) or 0)
        if duplicate_count > 0:
            add(
                {
                    "action": "drop_duplicates",
                    "column": "dataset",
                    "risk": "HIGH IMPACT",
                    "description": f"Remove {duplicate_count} exact duplicate row(s)",
                    "reason": "Rows are exact duplicates, but row removal is still destructive and requires explicit approval.",
                    "enabled_by_default": False,
                }
            )

        # Outliers are surfaced for review only; V1 never deletes/caps them automatically.
        for column, info in quality_report.get("outliers", {}).items():
            count = int(info.get("count", 0) or 0)
            if count <= 0:
                continue
            add(
                {
                    "action": "review_outliers",
                    "column": column,
                    "risk": "REVIEW",
                    "description": f"Review {count} potential outlier(s)",
                    "reason": "Outliers may be valid observations; DATA CLEAN reports them but does not alter them automatically.",
                    "enabled_by_default": False,
                    "executable": False,
                }
            )

        self.cleaning_plan = plan
        return plan


def run_cleaning_plan(file_path: str = "data/raw/sample_data.csv") -> str:
    """Build a cleaning plan for the CLI coordinator without modifying the dataset."""
    from ..tools.integrity_checker import IntegrityChecker
    from ..tools.quality_analyzer import QualityAnalyzer

    quality = QualityAnalyzer(file_path).run_full_profile()
    if "error" in quality:
        return f"Cleaning plan failed: {quality['error']}"
    integrity = IntegrityChecker(file_path).get_integrity_report()
    plan = SafeCleaningSkill().build_cleaning_plan(quality, integrity)
    if not plan:
        return "No cleaning or transformation actions were suggested."

    lines = ["DATA CLEAN - RISK-AWARE PLAN"]
    for step in plan:
        lines.append(
            f"- [{step['risk']}] {step['action']} on {step['column']}: {step['reason']}"
        )
    return "\n".join(lines)
