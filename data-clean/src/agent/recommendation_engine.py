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
