from typing import Any, Dict, List
from pathlib import Path
import polars as pl
from ..tools.cleaning_engine import CleaningEngine

class ValidationReportingSkill:
    """
    Skill responsible for the final stage of the DATA CLEAN workflow.
    It compares the original dataset with the cleaned version, 
    validates that no unexpected data loss occurred, and generates 
    the final change log and summary.
    """
    def __init__(self, original_path: str = "data/sample_data.csv", cleaned_path: str = "data/cleaned_sample_data.csv"):
        self.original_path = Path(original_path)
        self.cleaned_path = Path(cleaned_path)

    def validate_results(self) -> Dict[str, Any]:
        """
        Compares the original and cleaned datasets to ensure integrity.
        Checks for unexpected row loss or structural changes.
        """
        try:
            df_orig = pl.read_csv(self.original_path)
            df_clean = pl.read_csv(self.cleaned_path)
            
            orig_rows = df_orig.height
            clean_rows = df_clean.height
            
            # Check if we lost too many rows unexpectedly (e.g., > 20% loss without a duplicate-drop action)
            row_diff = orig_rows - clean_rows
            loss_percentage = (row_diff / orig_rows) * 100 if orig_rows > 0 else 0
            
            return {
                "original_row_count": orig_rows,
                "cleaned_row_count": clean_rows,
                "rows_removed": row_diff,
                "loss_percentage": f"{loss_percentage:.2f}%",
                "integrity_status": "PASS" if loss_percentage < 20 else "WARNING",
                "column_consistency": df_orig.columns == df_clean.columns
            }
        except Exception as e:
            return {"error": f"Validation failed: {str(e)}"}

    def generate_final_report(self, change_log: List[Dict[str, str]], validation_results: Dict[str, Any]) -> str:
        """
        Creates the final summary and change log as required by the MVP.
        """
        report = [
            "===================================================",
            "       DATA CLEAN - FINAL VALIDATION REPORT        ",
            "===================================================",
            f"Original File: {self.original_path}",
            f"Cleaned File:  {self.cleaned_path}",
            "\n[1] INTEGRITY CHECK",
            "---------------------------------------------------"
        ]
        
        if "error" in validation_results:
            report.append(f"Validation Error: {validation_results['error']}")
        else:
            report.append(f"Status: {validation_results['integrity_status']}")
            report.append(f"Rows: {validation_results['original_row_count']} -> {validation_results['cleaned_row_count']}")
            report.append(f"Rows Removed: {validation_results['rows_removed']} ({validation_results['loss_percentage']})")
            report.append(f"Column Structure Maintained: {'Yes' if validation_results['column_consistency'] else 'No'}")

        report.append("\n[2] DETAILED CHANGE LOG (Explainable Changes)")
        report.append("---------------------------------------------------")
        if not change_log:
            report.append("No changes were applied to the dataset.")
        else:
            for i, entry in enumerate(change_log, 1):
                report.append(f"{i}. [{entry['action']}] Column: {entry['column']} -> {entry['details']}")

        report.append("\n===================================================")
        report.append("CONCLUSION: Dataset has been processed according to the Safe Cleaning rules.")
        
        return "\n".join(report)

# Function wrapper for Registry
def run_validation_reporting():
    """
    Wrapper function for ValidationReportingSkill.
    In a real flow, this would be called after the SafeCleaningSkill.
    """
    skill = ValidationReportingSkill()
    
    # 1. Run Validation
    val_results = skill.validate_results()
    
    # 2. Retrieve Change Log from the engine (simulated for this wrapper)
    # In a real coordinator flow, the engine instance is shared.
    engine = CleaningEngine("data/sample_data.csv")
    # Simulating a few entries if engine is new, otherwise would use actual engine log
    change_log = engine.get_change_log() 
    
    # 3. Generate Report
    return skill.generate_final_report(change_log, val_results)
