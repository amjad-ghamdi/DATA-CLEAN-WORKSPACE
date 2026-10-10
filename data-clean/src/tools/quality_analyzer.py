import polars as pl
from pathlib import Path
from typing import Dict, Any, List

class QualityAnalyzer:
    """
    Technical tool for analyzing data quality and privacy using Polars.
    Aligns with the DATA CLEAN MVP: profiles rows, columns, and detects 
    privacy-sensitive fields and quality issues.
    """
    def __init__(self, file_path: str):
        self.file_path = Path(file_path)
        self.df = None
        # Common patterns for sensitive data (Privacy-aware)
        self.sensitive_keywords = {
            "email", "phone", "address", "password", "ssn", "credit_card", 
            "name", "birthdate", "salary", "iban", "national_id"
        }

    def load_data(self) -> bool:
        """Loads the CSV file into a Polars DataFrame."""
        try:
            if self.file_path.suffix.lower() == '.csv':
                self.df = pl.read_csv(self.file_path)
                return True
            else:
                return False
        except Exception as e:
            print(f"Error loading file: {e}")
            return False

    def analyze_privacy(self) -> Dict[str, Any]:
        """
        Identifies potentially sensitive columns based on naming conventions.
        This is the first step in the 'Privacy-aware processing' requirement.
        """
        if self.df is None:
            return {"error": "Data not loaded"}
        
        sensitive_cols = []
        for col in self.df.columns:
            col_lower = col.lower()
            if any(keyword in col_lower for keyword in self.sensitive_keywords):
                sensitive_cols.append(col)
        
        return {
            "sensitive_columns": sensitive_cols,
            "privacy_risk": "High" if len(sensitive_cols) > 0 else "Low",
            "count": len(sensitive_cols)
        }

    def analyze_missing_values(self) -> Dict[str, Any]:
        """Calculates the percentage of missing values per column."""
        if self.df is None:
            return {"error": "Data not loaded"}
        
        null_counts = self.df.null_count()
        total_rows = self.df.height
        
        missing_report = {}
        for col in self.df.columns:
            count = null_counts[0, self.df.columns.index(col)]
            percentage = (count / total_rows) * 100 if total_rows > 0 else 0
            missing_report[col] = {"count": count, "percentage": f"{percentage:.2f}%"}
            
        return missing_report

    def analyze_inconsistencies(self) -> Dict[str, Any]:
        """
        Detects inconsistent values such as 'Male' / 'male' / 'MALE' 
        and whitespace issues.
        """
        if self.df is None:
            return {"error": "Data not loaded"}
            
        inconsistencies = {}
        for col in self.df.columns:
            # Only check categorical/string columns
            if self.df[col].dtype == pl.Utf8:
                # Find unique values and their casing variants
                unique_vals = self.df[col].unique().to_list()
                # Group values by their lowercase version
                variants = {}
                for val in unique_vals:
                    if val is None: continue
                    low_val = str(val).lower().strip()
                    variants.setdefault(low_val, []).append(val)
                
                # If any lowercase group has more than one variant, it's inconsistent
                inconsistent_groups = {k: v for k, v in variants.items() if len(v) > 1}
                if inconsistent_groups:
                    inconsistencies[col] = inconsistent_groups
                    
        return inconsistencies

    def analyze_duplicates(self) -> Dict[str, Any]:
        """Checks for duplicate rows in the dataset."""
        if self.df is None:
            return {"error": "Data not loaded"}
            
        total_rows = self.df.height
        unique_rows = self.df.unique().height
        duplicates = total_rows - unique_rows
        
        return {
            "total_rows": total_rows,
            "unique_rows": unique_rows,
            "duplicate_rows": duplicates,
            "percentage": f"{(duplicates / total_rows * 100 if total_rows > 0 else 0):.2f}%"
        }

    def get_schema_info(self) -> Dict[str, str]:
        """Returns the data types of all columns."""
        if self.df is None:
            return {"error": "Data not loaded"}
            
        return {col: str(dtype) for col, dtype in self.df.schema.items()}

    def run_full_profile(self) -> Dict[str, Any]:
        """
        Executes the complete profiling sequence as defined in the MVP.
        1. Privacy Check -> 2. Profiling -> 3. Issue Detection.
        """
        if not self.load_data():
            return {"error": "Could not load data for profiling"}
            
        return {
            "privacy": self.analyze_privacy(),
            "missing_values": self.analyze_missing_values(),
            "inconsistencies": self.analyze_inconsistencies(),
            "duplicates": self.analyze_duplicates(),
            "schema": self.get_schema_info()
        }
