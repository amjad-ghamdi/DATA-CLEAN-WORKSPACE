from typing import Any, Dict
from ..tools.quality_analyzer import QualityAnalyzer

class DataQualitySkill:
    """
    Skill responsible for assessing data quality.
    It uses the QualityAnalyzer tool and transforms the technical 
    results into a human-readable report, including privacy and 
    inconsistency checks as per the MVP.
    """
    def __init__(self, file_path: str = "data/sample_data.csv"):
        self.file_path = file_path

    def execute(self) -> str:
        """
        Executes the quality assessment flow.
        """
        print(f"Skill: Assessing quality and privacy for file {self.file_path}...")
        
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
            "===================================================",
            "       DATA CLEAN - QUALITY ANALYSIS REPORT       ",
            "===================================================",
            f"File: {self.file_path}",
            "\n[1] PRIVACY & SENSITIVITY CHECK",
            "---------------------------------------------------"
        ]
        
        # Format privacy
        privacy = data.get('privacy', {})
        report.append(f"Risk Level: {privacy.get('privacy_risk', 'Unknown')}")
        sensitive_cols = privacy.get('sensitive_columns', [])
        if sensitive_cols:
            report.append(f"Sensitive Columns Detected: {', '.join(sensitive_cols)}")
        else:
            report.append("No sensitive columns detected based on keywords.")
            
        # Format missing values
        report.append("\n[2] MISSING VALUES ANALYSIS")
        report.append("---------------------------------------------------")
        for col, info in data.get('missing_values', {}).items():
            report.append(f" - {col}: {info['count']} missing ({info['percentage']})")
            
        # Format inconsistencies
        report.append("\n[3] DATA INCONSISTENCIES (Casing/Whitespace)")
        report.append("---------------------------------------------------")
        inconsistencies = data.get('inconsistencies', {})
        if inconsistencies:
            for col, variants in inconsistencies.items():
                report.append(f" - Column '{col}' has inconsistent values:")
                for low_val, versions in variants.items():
                    report.append(f"   * Found variants: {', '.join(versions)}")
        else:
            report.append("No major casing or whitespace inconsistencies detected.")
            
        # Format duplicates
        report.append("\n[4] DUPLICATES ANALYSIS")
        report.append("---------------------------------------------------")
        dupes = data.get('duplicates', {})
        report.append(f" - Total Rows: {dupes.get('total_rows', 'N/A')}")
        report.append(f" - Duplicate Rows: {dupes.get('duplicate_rows', 'N/A')} ({dupes.get('percentage', 'N/A')})")
        
        # Format schema
        report.append("\n[5] DATA SCHEMA")
        report.append("---------------------------------------------------")
        for col, dtype in data.get('schema', {}).items():
            report.append(f" - {col}: {dtype}")
            
        report.append("\n===================================================")
        return "\n".join(report)

# Function wrapper to be registered in the Registry
def run_data_quality_analysis():
    """Wrapper function for the DataQualitySkill."""
    # Defaulting to sample_data.csv for now
    skill = DataQualitySkill(file_path="data/sample_data.csv")
    return skill.execute()
