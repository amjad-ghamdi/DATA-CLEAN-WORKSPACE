from typing import Any, Dict, List, Tuple
from ..tools.quality_analyzer import QualityAnalyzer
from ..tools.cleaning_engine import CleaningEngine

class SafeCleaningSkill:
    """
    Skill responsible for implementing the 'Safe Cleaning' logic as per the MVP.
    It classifies cleaning actions into SAFE, REVIEW, and HIGH IMPACT,
    ensuring that only safe changes are applied automatically.
    """
    def __init__(self, file_path: str = "data/sample_data.csv"):
        self.file_path = file_path
        self.engine = CleaningEngine(file_path)
        self.cleaning_plan = []

    def build_cleaning_plan(self, quality_report: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Analyzes the quality report and builds a risk-aware cleaning plan.
        Classifies actions as: SAFE, REVIEW, or HIGH IMPACT.
        """
        plan = []
        
        # 1. Handle Whitespace (Always SAFE)
        # We suggest trimming all string columns that have data
        schema = quality_report.get('schema', {})
        string_cols = [col for col, dtype in schema.items() if "Utf8" in dtype]
        if string_cols:
            plan.append({
                "action": "trim_whitespace",
                "columns": string_cols,
                "risk": "SAFE",
                "reason": "Removing leading/trailing whitespace does not change semantic meaning."
            })

        # 2. Handle Inconsistencies (REVIEW)
        inconsistencies = quality_report.get('inconsistencies', {})
        for col, variants in inconsistencies.items():
            plan.append({
                "action": "normalize_casing",
                "column": col,
                "risk": "REVIEW",
                "reason": f"Detected {len(variants)} different casing variants. Needs verification."
            })

        # 3. Handle Missing Values (REVIEW/HIGH IMPACT)
        missing = quality_report.get('missing_values', {})
        for col, info in missing.items():
            if info['count'] > 0:
                # If missing values are very high (>50%), it's HIGH IMPACT to fill or drop
                risk = "HIGH IMPACT" if float(info['percentage'].strip('%')) > 50 else "REVIEW"
                plan.append({
                    "action": "fill_nulls",
                    "column": col,
                    "risk": risk,
                    "reason": f"Column has {info['percentage']} missing values. Imputation strategy required."
                })

        # 4. Handle Duplicates (HIGH IMPACT)
        dupes = quality_report.get('duplicates', {})
        if dupes.get('duplicate_rows', 0) > 0:
            plan.append({
                "action": "drop_duplicates",
                "columns": None, # All columns
                "risk": "HIGH IMPACT",
                "reason": f"Found {dupes.get('duplicate_rows')} duplicate rows. Dropping might lose data."
            })

        self.cleaning_plan = plan
        return plan

    def execute_safe_changes(self) -> Tuple[str, List[Dict[str, Any]]]:
        """
        Executes only the actions marked as 'SAFE'.
        Returns a summary and a list of actions that still require REVIEW.
        """
        if not self.engine.load_data():
            return "Error: Could not load data for cleaning", []

        executed_count = 0
        pending_review = []

        for step in self.cleaning_plan:
            if step['risk'] == "SAFE":
                # Execute based on action name
                action = step['action']
                if action == "trim_whitespace":
                    self.engine.trim_whitespace(step['columns'])
                
                executed_count += 1
            else:
                pending_review.append(step)

        # Save the result of safe changes
        output_path = self.file_path.parent / f"cleaned_{self.file_path.name}"
        self.engine.save_cleaned_data(str(output_path))

        summary = f"Safe cleaning complete. {executed_count} actions applied automatically. File saved to {output_path}."
        return summary, pending_review

# Function wrapper for Registry
def run_safe_cleaning():
    """
    Wrapper function for SafeCleaningSkill.
    In a real scenario, this would take the quality_report from the previous stage.
    """
    skill = SafeCleaningSkill()
    
    # Step 1: Simulate getting report from the Analysis stage
    analyzer = QualityAnalyzer(skill.file_path)
    report = analyzer.run_full_profile()
    
    # Step 2: Build plan
    plan = skill.build_cleaning_plan(report)
    
    # Step 3: Execute safe parts
    summary, review_list = skill.execute_safe_changes()
    
    # Combine result for the coordinator
    full_result = f"{summary}\n\nPending Review:\n"
    for item in review_list:
        full_result += f"- {item['action']} on {item.get('column', 'dataset')} [{item['risk']}]: {item['reason']}\n"
        
    return full_result
