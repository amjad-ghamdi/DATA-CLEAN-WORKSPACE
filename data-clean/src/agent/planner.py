from typing import Any, Dict, List, Optional

class Planner:
    """
    The Planner is responsible for analyzing the user's request and 
    breaking it down into a sequence of executable tasks.
    """
    def __init__(self, registry):
        self.registry = registry

    def plan(self, user_query: str) -> List[Dict[str, Any]]:
        """
        Analyzes the query and returns a list of steps.
        In a production version, this would call an LLM.
        For now, we implement a keyword-based logic for initial testing.
        """
        plan = []
        query = user_query.lower()

        # Basic heuristic planning (To be replaced by LLM logic)
        if "جودة" in query or "quality" in query:
            plan.append({
                "step": 1,
                "action": "assess_quality",
                "type": "skill",
                "description": "Measure the general data quality of the dataset."
            })
        
        if "تنظيف" in query or "clean" in query:
            plan.append({
                "step": 2,
                "action": "clean_data",
                "type": "skill",
                "description": "Perform cleaning operations on the dataset."
            })

        if not plan:
            # Default fallback step
            plan.append({
                "step": 1,
                "action": "general_analysis",
                "type": "skill",
                "description": "Perform a general analysis of the data."
            })

        return plan

# Instance to be used by the coordinator
planner = Planner(registry=None) # Registry will be injected during initialization
