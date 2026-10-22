from typing import Any, Dict


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
