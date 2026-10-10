from typing import Any, Dict
from ..tools.quality_analyzer import QualityAnalyzer

class DataQualitySkill:
    """
    Skill responsible for assessing data quality.
    It uses the QualityAnalyzer tool and transforms the technical 
    results into a human-readable report.
    """
    def __init__(self, file_path: str = "data/sample_data.csv"):
        self.file_path = file_path

    def execute(self) -> str:
        """
        Executes the quality assessment flow.
        """
        print(f"Skill: Assessing quality for file {self.file_path}...")
        
        try:
            # Use the tool for technical analysis
            analyzer = QualityAnalyzer(self.file_path)
            report_data = analyzer.run_full_profile()
            
            if "error" in report_data:
                return f"Quality Assessment failed: {report_data['error']}"
            
            # Transform technical data into a readable report
            return self._format_report(report_data)
            
        except Exception as e:
            return f"An unexpected error occurred during quality assessment: {str(e)}"

    def _format_report(self, data: Dict[str, Any]) -> str:
        """Formats the raw analyzer data into a clean string report."""
        report = [
            "--- Data Quality Assessment Report ---",
            f"File: {self.file_path}",
            "\n[1] Missing Values Analysis:"
        ]
        
        # Format missing values
        for col, info in data['missing_values'].items():
            report.append(f" - {col}: {info['count']} missing ({info['percentage']})")
            
        # Format duplicates
        dupes = data['duplicates']
        report.append("\n[2] Duplicates Analysis:")
        report.append(f" - Total Rows: {dupes['total_rows']}")
        report.append(f" - Duplicate Rows: {dupes['duplicate_rows']} ({dupes['percentage']})")
        
        # Format schema
        report.append("\n[3] Schema Information:")
        for col, dtype in data['schema'].items():
            report.append(f" - {col}: {dtype}")
            
        report.append("\n-------------------------------------")
        return "\n".join(report)

# Function wrapper to be registered in the Registry
def run_data_quality_analysis():
    """Wrapper function for the DataQualitySkill."""
    # Defaulting to sample_data.csv for now
    skill = DataQualitySkill(file_path="data/sample_data.csv")
    return skill.execute()
