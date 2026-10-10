import polars as pl
from pathlib import Path
from typing import Dict, Any

class QualityAnalyzer:
    """
    Technical tool for analyzing data quality using Polars.
    Focuses on identifying missing values, duplicates, and data type consistency.
    """
    def __init__(self, file_path: str):
        self.file_path = Path(file_path)
        self.df = None

    def load_data(self) -> bool:
        """Loads the CSV file into a Polars DataFrame."""
        try:
            if self.file_path.suffix.lower() == '.csv':
                self.df = pl.read_csv(self.file_path)
                return True
            else:
                # For simplicity in this version, we focus on CSV
                return False
        except Exception as e:
            print(f"Error loading file: {e}")
            return False

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
        """Executes all analysis methods and returns a combined report."""
        if not self.load_data():
            return {"error": "Could not load data for profiling"}
            
        return {
            "missing_values": self.analyze_missing_values(),
            "duplicates": self.analyze_duplicates(),
            "schema": self.get_schema_info()
        }
