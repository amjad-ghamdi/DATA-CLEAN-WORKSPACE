"""Deterministic recommendations, explanations and cleaning-plan construction.

This module does not train a model or modify the input dataset.
"""
from typing import Any, Dict, List, Optional, Set


class RecommendationEngine:
    """Choose ranked, explainable cleaning strategies from profile evidence."""

    NUMERIC_MARKERS = ("int", "float", "decimal")

    @staticmethod
    def _is_numeric(dtype: str) -> bool:
        normalized = str(dtype).lower()
        return any(marker in normalized for marker in RecommendationEngine.NUMERIC_MARKERS)

    @staticmethod
    def _is_string(dtype: str) -> bool:
        normalized = str(dtype).lower()
        return "string" in normalized or "utf8" in normalized

    @staticmethod
    def _option(value: str, label: str, recommended: bool = False, caution: str = "") -> Dict[str, Any]:
        return {
            "value": value,
            "label": label,
            "recommended": recommended,
            "caution": caution,
        }

    def recommend_missing(
        self,
        column: str,
        dtype: str,
        missing_info: Dict[str, Any],
        column_profile: Dict[str, Any],
        all_columns: Set[str],
        protected_columns: Set[str],
    ) -> Dict[str, Any]:
        percentage = float(missing_info.get("percentage_value", 0.0) or 0.0)
        unique_ratio = float(column_profile.get("unique_ratio", 1.0) or 1.0)
        lowered = column.lower()

        if column in protected_columns:
            return {
                "strategy": "keep",
                "strategy_options": [
                    self._option("keep", "Keep missing", True),
                    self._option("drop", "Drop affected rows", False, "High impact"),
                ],
                "risk": "HIGH IMPACT",
                "confidence": "High",
                "reason_code": "protected_identifier",
                "reason": "This column may identify rows, so automatic imputation is blocked.",
                "auto_eligible": False,
            }

        if (
            lowered in {"number_of_seasons", "number_of_episodes"}
            and "content_type" in all_columns
        ):
            label = "Estimate only for series-type rows"
            return {
                "strategy": "contextual_median",
                "strategy_options": [
                    self._option("contextual_median", label, True),
                    self._option("keep", "Keep missing"),
                    self._option("median", "Use one median for every row", False, "May assign values where they do not apply"),
                    self._option("mean", "Use one mean for every row", False, "May create unrealistic fractional counts"),
                    self._option("drop", "Drop affected rows", False, "High impact"),
                ],
                "risk": "REVIEW",
                "confidence": "High",
                "reason_code": "context_dependent_count",
                "reason": "This count only applies to series-like content; non-series rows should stay empty.",
                "auto_eligible": True,
                "context_column": "content_type",
                "context_values": ["series", "tv"],
                "statistic": "median",
                "round_result": True,
            }

        if self._is_numeric(dtype):
            if percentage <= 20:
                confidence = "High" if percentage <= 10 else "Medium"
                return {
                    "strategy": "median",
                    "strategy_options": [
                        self._option("median", "Median", True),
                        self._option("mean", "Mean"),
                        self._option("keep", "Keep missing"),
                        self._option("constant", "Use a constant"),
                        self._option("drop", "Drop affected rows", False, "High impact"),
                    ],
                    "risk": "REVIEW",
                    "confidence": confidence,
                    "reason_code": "numeric_low_missingness",
                    "reason": "Missingness is limited; the median is robust to extreme values.",
                    "auto_eligible": True,
                }

            return {
                "strategy": "keep",
                "strategy_options": [
                    self._option("keep", "Keep missing", True),
                    self._option("median", "Median", False, "May hide substantial missingness"),
                    self._option("mean", "Mean", False, "Sensitive to extreme values"),
                    self._option("constant", "Use a numeric constant"),
                    self._option("drop", "Drop affected rows", False, "High impact"),
                ],
                "risk": "REVIEW",
                "confidence": "High",
                "reason_code": "numeric_high_missingness",
                "reason": "A large share of values is missing, so guessing them could distort the dataset.",
                "auto_eligible": False,
            }

        if self._is_string(dtype) and percentage <= 10 and unique_ratio <= 0.20:
            return {
                "strategy": "mode",
                "strategy_options": [
                    self._option("mode", "Most common category", True),
                    self._option("keep", "Keep missing"),
                    self._option("constant", "Use a custom label"),
                    self._option("drop", "Drop affected rows", False, "High impact"),
                ],
                "risk": "REVIEW",
                "confidence": "Medium",
                "reason_code": "categorical_low_missingness",
                "reason": "Only a small share is missing and the column behaves like a category.",
                "auto_eligible": True,
            }

        return {
            "strategy": "keep",
            "strategy_options": [
                self._option("keep", "Keep missing", True),
                self._option("mode", "Most common category"),
                self._option("constant", "Use a custom label"),
                self._option("drop", "Drop affected rows", False, "High impact"),
            ],
            "risk": "REVIEW",
            "confidence": "High",
            "reason_code": "categorical_high_missingness",
            "reason": "Missingness is substantial or the meaning is ambiguous, so preserving nulls is safest.",
            "auto_eligible": False,
        }

    def recommend_outliers(self, column: str, info: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "strategy": "keep",
            "strategy_options": [
                self._option("keep", "Keep unchanged", True),
                self._option("winsorize", "Cap to the detected IQR limits"),
                self._option("drop", "Drop outlier rows", False, "High impact"),
            ],
            "risk": "REVIEW",
            "confidence": "High",
            "reason_code": "outlier_review",
            "reason": "Unusual values can be valid observations, so keeping them is safest unless reviewed.",
            "auto_eligible": False,
            "lower_bound": info.get("lower_bound"),
            "upper_bound": info.get("upper_bound"),
        }


class ExplanationAgent:
    """Turn structured cleaning decisions into simple or technical explanations.

    This layer never decides what to clean. It only explains decisions produced
    by the deterministic recommendation engine.
    """

    GUIDED_MESSAGES = {
        "protected_identifier": (
            "This column may identify records, so DATA CLEAN will not guess missing values automatically."
        ),
        "context_dependent_count": (
            "These values only make sense for series-like content. Movies and other non-series rows stay empty."
        ),
        "numeric_low_missingness": (
            "Only a limited share is missing, so DATA CLEAN recommends a robust estimate that is less affected by extreme values."
        ),
        "numeric_high_missingness": (
            "Too much of this column is missing to safely guess values. Keeping them empty is the safer choice."
        ),
        "categorical_low_missingness": (
            "Only a small share is missing, so DATA CLEAN can use the most common existing category after review."
        ),
        "categorical_high_missingness": (
            "A large share is missing or the meaning is unclear, so keeping values empty avoids misleading guesses."
        ),
        "outlier_review": (
            "These unusual values may still be valid. DATA CLEAN keeps them unchanged unless you choose another option."
        ),
    }

    @classmethod
    def explain(cls, step: Dict[str, Any], mode: str = "guided") -> Dict[str, str]:
        reason_code = str(step.get("reason_code", ""))
        confidence = str(step.get("confidence", ""))
        recommendation = str(step.get("recommended_label") or step.get("strategy") or "Review")

        if mode == "expert":
            detail = step.get("reason") or "Review the detected issue before applying a transformation."
            return {
                "summary": detail,
                "why": f"Recommended: {recommendation}. Confidence: {confidence or 'Not scored'}.",
            }

        summary = cls.GUIDED_MESSAGES.get(
            reason_code,
            step.get("reason") or "DATA CLEAN found an issue that may need your review.",
        )
        why = f"Recommended: {recommendation}"
        if confidence:
            why += f" · Confidence: {confidence}"
        return {"summary": summary, "why": why}


class SafeCleaningSkill:
    """Build a ranked, explainable cleaning and transformation plan."""

    NUMERIC_MARKERS = ("int", "float", "decimal")

    def __init__(self):
        self.cleaning_plan: List[Dict[str, Any]] = []
        self.recommender = RecommendationEngine()

    @staticmethod
    def _is_numeric(dtype: str) -> bool:
        normalized = str(dtype).lower()
        return any(marker in normalized for marker in SafeCleaningSkill.NUMERIC_MARKERS)

    @staticmethod
    def _is_string(dtype: str) -> bool:
        normalized = str(dtype).lower()
        return "utf8" in normalized or "string" in normalized

    @staticmethod
    def _recommended_label(options: List[Dict[str, Any]], fallback: str) -> str:
        for option in options:
            if option.get("recommended"):
                return str(option.get("label") or option.get("value") or fallback)
        return fallback

    def build_cleaning_plan(
        self,
        quality_report: Dict[str, Any],
        integrity_report: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        integrity_report = integrity_report or {}
        protected_columns = set(integrity_report.get("protected_columns", []))
        schema = quality_report.get("schema", {})
        column_profiles = quality_report.get("column_profiles", {})
        all_columns = set(schema.keys())
        plan: List[Dict[str, Any]] = []
        step = 1

        def add(item: Dict[str, Any]) -> None:
            nonlocal step
            item["id"] = f"step-{step}"
            item.setdefault("confidence", "High" if item.get("risk") == "SAFE" else "Medium")
            item.setdefault("auto_eligible", item.get("risk") == "SAFE")
            item.setdefault("enabled_by_default", item.get("risk") == "SAFE")
            item.setdefault("executable", True)
            item.setdefault("strategy_options", [])
            item.setdefault("recommended_label", item.get("strategy") or item.get("description"))
            item["guided_explanation"] = ExplanationAgent.explain(item, "guided")
            item["expert_explanation"] = ExplanationAgent.explain(item, "expert")
            plan.append(item)
            step += 1

        # Safe textual cleanup.
        for column, dtype in schema.items():
            if self._is_string(dtype):
                protected = column in protected_columns
                add(
                    {
                        "action": "trim_whitespace",
                        "column": column,
                        "risk": "HIGH IMPACT" if protected else "SAFE",
                        "description": "Remove unnecessary spaces",
                        "reason_code": "safe_whitespace",
                        "reason": "Leading and trailing whitespace can create false category differences.",
                        "strategy": "trim",
                        "recommended_label": "Trim unnecessary spaces",
                        "auto_eligible": not protected,
                        "enabled_by_default": not protected,
                    }
                )

        # Casing normalization remains a reviewed recommendation.
        for column, variants in quality_report.get("inconsistencies", {}).items():
            risk = "HIGH IMPACT" if column in protected_columns else "REVIEW"
            add(
                {
                    "action": "normalize_casing",
                    "column": column,
                    "risk": risk,
                    "description": "Standardize inconsistent text formatting",
                    "reason_code": "category_formatting",
                    "reason": f"Detected {len(variants)} equivalent value group(s) with different casing or spacing.",
                    "target_case": "title",
                    "strategy": "title",
                    "strategy_options": [
                        {"value": "title", "label": "Title case", "recommended": True},
                        {"value": "lower", "label": "lowercase", "recommended": False},
                        {"value": "upper", "label": "UPPERCASE", "recommended": False},
                    ],
                    "recommended_label": "Standardize matching category labels",
                    "confidence": "High",
                    "auto_eligible": True,
                    "enabled_by_default": False,
                }
            )

        # Ranked, context-aware missing-value strategies.
        for column, info in quality_report.get("missing_values", {}).items():
            count = int(info.get("count", 0) or 0)
            if count == 0:
                continue

            rec = self.recommender.recommend_missing(
                column=column,
                dtype=schema.get(column, ""),
                missing_info=info,
                column_profile=column_profiles.get(column, {}),
                all_columns=all_columns,
                protected_columns=protected_columns,
            )
            options = rec.get("strategy_options", [])
            item = {
                "action": "fill_nulls",
                "column": column,
                "risk": rec["risk"],
                "description": f"Handle {count} missing value(s)",
                "reason_code": rec["reason_code"],
                "reason": rec["reason"],
                "strategy": rec["strategy"],
                "strategy_options": options,
                "recommended_label": self._recommended_label(options, rec["strategy"]),
                "fill_value": None,
                "confidence": rec.get("confidence", "Medium"),
                "auto_eligible": bool(rec.get("auto_eligible", False)),
                "enabled_by_default": False,
            }
            for key in ("context_column", "context_values", "statistic", "round_result"):
                if key in rec:
                    item[key] = rec[key]
            add(item)

        # Conservative type transformations.
        for candidate in quality_report.get("transformation_candidates", []):
            column = candidate["column"]
            risk = "HIGH IMPACT" if column in protected_columns else "REVIEW"
            action = candidate["action"]
            label = (
                "Convert numeric-looking text to numbers"
                if action == "coerce_numeric"
                else "Standardize recognized dates"
            )
            add(
                {
                    "action": action,
                    "column": column,
                    "risk": risk,
                    "description": f"Convert to {candidate['target_type']}",
                    "reason_code": "type_transformation",
                    "reason": candidate["reason"],
                    "confidence": (
                        "High" if float(candidate.get("confidence", 0)) >= 0.95 else "Medium"
                    ),
                    "recommended_label": label,
                    "auto_eligible": risk != "HIGH IMPACT",
                    "enabled_by_default": False,
                }
            )

        # Exact duplicates are a strong recommendation but still destructive.
        duplicates = quality_report.get("duplicates", {})
        duplicate_count = int(duplicates.get("duplicate_rows", 0) or 0)
        if duplicate_count > 0:
            add(
                {
                    "action": "drop_duplicates",
                    "column": "dataset",
                    "risk": "REVIEW",
                    "description": f"Remove {duplicate_count} exact duplicate row(s)",
                    "reason_code": "exact_duplicates",
                    "reason": "These rows are exact copies of other rows, so removing the repeated copies preserves unique information.",
                    "confidence": "High",
                    "recommended_label": "Remove exact duplicates",
                    "auto_eligible": False,
                    "enabled_by_default": False,
                }
            )

        # Outliers now expose reviewed options instead of being permanently disabled.
        for column, info in quality_report.get("outliers", {}).items():
            count = int(info.get("count", 0) or 0)
            if count <= 0:
                continue
            rec = self.recommender.recommend_outliers(column, info)
            options = rec["strategy_options"]
            add(
                {
                    "action": "handle_outliers",
                    "column": column,
                    "risk": rec["risk"],
                    "description": f"Review {count} potential outlier(s)",
                    "reason_code": rec["reason_code"],
                    "reason": rec["reason"],
                    "strategy": rec["strategy"],
                    "strategy_options": options,
                    "recommended_label": self._recommended_label(options, rec["strategy"]),
                    "confidence": rec["confidence"],
                    "auto_eligible": rec["auto_eligible"],
                    "enabled_by_default": False,
                    "lower_bound": rec.get("lower_bound"),
                    "upper_bound": rec.get("upper_bound"),
                }
            )

        self.cleaning_plan = plan
        return plan


def run_cleaning_plan(file_path: str = "data/raw/sample_data.csv") -> str:
    from src.tools.integrity_checker import IntegrityChecker
    from src.tools.quality_analyzer import QualityAnalyzer

    quality = QualityAnalyzer(file_path).run_full_profile()
    if "error" in quality:
        return f"Cleaning plan failed: {quality['error']}"
    integrity = IntegrityChecker(file_path).get_integrity_report()
    plan = SafeCleaningSkill().build_cleaning_plan(quality, integrity)
    if not plan:
        return "No cleaning or transformation actions were suggested."

    lines = ["DATA CLEAN - RECOMMENDED PLAN"]
    for step in plan:
        lines.append(
            f"- [{step['risk']}] {step['column']}: {step['recommended_label']} "
            f"(confidence: {step.get('confidence', 'n/a')})"
        )
    return "\n".join(lines)
