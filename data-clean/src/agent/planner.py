from typing import Any, Dict, List


class Planner:
    """Deterministic request router for the local CLI agent."""

    def __init__(self, registry):
        self.registry = registry

    def plan(self, user_query: str) -> List[Dict[str, Any]]:
        query = user_query.lower()
        plan: List[Dict[str, Any]] = []

        if any(token in query for token in ("جودة", "quality", "profile", "analyze", "تحليل")):
            plan.append(
                {
                    "step": len(plan) + 1,
                    "action": "assess_quality",
                    "type": "skill",
                    "description": "Profile dataset quality, privacy, and transformation candidates.",
                }
            )

        if any(token in query for token in ("تنظيف", "clean", "transform", "تحويل", "plan")):
            plan.append(
                {
                    "step": len(plan) + 1,
                    "action": "plan_cleaning",
                    "type": "skill",
                    "description": "Build a risk-aware cleaning and transformation plan.",
                }
            )

        if not plan:
            plan.append(
                {
                    "step": 1,
                    "action": "assess_quality",
                    "type": "skill",
                    "description": "Default to a read-only quality profile.",
                }
            )

        return plan
