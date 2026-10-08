import polars as pl
from pathlib import Path
from typing import Dict, Any, List

class DataProfiler:
    """
    Professional data profiling tool using Polars for high-performance 
    analysis of CSV and Excel datasets.
    """
    def __init__(self, file_path: str):
        self.file_path = Path(file_path)
        self.df = self._load_data()

    def _load_data(self) -> pl.DataFrame:
        """Loads data based on file extension."""
        ext = self.file_path.suffix.lower()
        if ext == '.csv':
            return pl.read_csv(self.file_path)
        elif ext in ['.xlsx', '.xls']:
            return pl.read_excel(self.file_path)
        else:
            raise ValueError(f"Unsupported file format: {ext}. Please provide CSV or Excel files.")

    def profile(self) -> Dict[str, Any]:
        """
        Generates a comprehensive profile of the dataset.
        Returns a dictionary containing structural and quality metrics.
        """
        if self.df is None:
            return {"error": "Data not loaded"}

        # Basic dimensions
        rows = self.df.height
        cols = self.df.width

        # Analysis per column
        column_analysis = {}
        for col_name in self.df.columns:
            column = self.df[col_name]
            
            # Null analysis
            null_count = column.null_count()
            null_percentage = (null_count / rows) * 100 if rows > 0 else 0
            
            # Unique values and consistency
            unique_count = column.n_unique()
            
            # Basic stats based on type
            dtype = column.dtype
            stats = {}
            if dtype.is_numeric():
                stats = {
                    "min": column.min(),
                    "max": column.max(),
                    "mean": column.mean(),
                    "median": column.median()
                }
            elif dtype == pl.Utf8: # String type in Polars
                # For strings, we look at the most common value
                common = column.mode().to_list()
                stats = {"most_common": common[0] if common else None}

            column_analysis[col_name] = {
                "dtype": str(dtype),
                "null_count": null_count,
                "null_percentage": f"{null_percentage:.2f}%",
                "unique_count": unique_count,
                "stats": stats
            }

        # Global duplicates
        duplicates_count = rows - self.df.unique().height

        return {
            "dataset_summary": {
                "total_rows": rows,
                "total_columns": cols,
                "total_duplicates": duplicates_count,
                "duplicate_percentage": f"{(duplicates_count / rows * 100 if rows > 0 else 0):.2f}%"
            },
            "columns_analysis": column_analysis
        }

if __name__ == "__main__":
    # This part is for local testing by the developer
    print("DataProfiler initialized. Ready for integration with the Agent.")
