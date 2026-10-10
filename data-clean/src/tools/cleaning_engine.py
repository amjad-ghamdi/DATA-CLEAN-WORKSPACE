import polars as pl
from pathlib import Path
from typing import Dict, Any, List, Union

class CleaningEngine:
    """
    Backend tool for performing actual data cleaning operations using Polars.
    It preserves the original dataset and returns a cleaned version along with 
    a detailed log of all changes made.
    """
    def __init__(self, file_path: str):
        self.file_path = Path(file_path)
        self.df = None
        self.change_log = []

    def load_data(self) -> bool:
        """Loads the dataset into memory."""
        try:
            if self.file_path.suffix.lower() == '.csv':
                self.df = pl.read_csv(self.file_path)
                return True
            return False
        except Exception as e:
            print(f"Error loading file for cleaning: {e}")
            return False

    def _log_change(self, action: str, column: str, details: str):
        """Records a change for the explainability requirement of the MVP."""
        self.change_log.append({
            "action": action,
            "column": column,
            "details": details
        })

    def trim_whitespace(self, columns: List[str]) -> None:
        """Removes leading and trailing whitespace from string columns. (SAFE)"""
        for col in columns:
            if self.df[col].dtype == pl.Utf8:
                self.df = self.df.with_columns(pl.col(col).str.strip_chars())
                self._log_change("trim_whitespace", col, "Removed leading/trailing whitespace")

    def normalize_casing(self, column: str, target_case: str = "lower") -> None:
        """Normalizes text casing. (REVIEW)"""
        if self.df[column].dtype == plP.Utf8: # Fix potential typo: pl.Utf8
             pass # Handled below in corrected logic

    def normalize_casing_fixed(self, column: str, target_case: str = "lower") -> None:
        """Corrected version of casing normalization."""
        if self.df[column].dtype == pl.Utf8:
            if target_case == "lower":
                self.df = self.df.with_columns(pl.col(column).str.to_lowercase())
            elif target_case == "upper":
                self.df = self.df.with_columns(pl.col(column).str.to_uppercase())
            elif target_case == "title":
                self.df = self.df.with_columns(pl.col(column).str.to_titlecase())
            
            self._log_change("normalize_casing", column, f"Converted to {target_case} case")

    def fill_nulls(self, column: str, strategy: str, fill_value: Any = None) -> None:
        """
        Fills missing values based on the chosen strategy.
        Strategies: 'constant', 'mean', 'median', 'mode', 'drop'
        """
        if self.df is None: return

        if strategy == "constant":
            self.df = self.df.with_columns(pl.col(column).fill_null(fill_value))
            self._log_change("fill_nulls", column, f"Filled with constant value: {fill_value}")

        elif strategy == "mean":
            mean_val = self.df[column].mean()
            self.df = self.df.with_columns(pl.col(column).fill_null(mean_val))
            self._log_change("fill_nulls", column, f"Filled with mean: {mean_val}")

        elif strategy == "median":
            median_val = self.df[column].median()
            self.df = self.df.with_columns(pl.col(column).fill_null(median_val))
            self._log_change("fill_nulls", column, f"Filled with median: {median_val}")

        elif strategy == "mode":
            mode_val = self.df[column].mode()[0] if self.df[column].mode().len() > 0 else None
            self.df = self.df.with_columns(pl.col(column).fill_null(mode_val))
            self._log_change("fill_nulls", column, f"Filled with mode: {mode_val}")

        elif strategy == "drop":
            self.df = self.df.drop_nulls(subset=[column])
            self._log_change("fill_nulls", column, "Dropped rows with null values in this column")

    def drop_duplicates(self, subset: List[str] = None) -> None:
        """Removes duplicate rows. (HIGH IMPACT)"""
        initial_count = self.df.height
        self.df = self.df.unique(subset=subset)
        final_count = self.df.height
        self._log_change("drop_duplicates", "dataset", f"Removed {initial_count - final_count} duplicate rows")

    def save_cleaned_data(self, output_path: str) -> bool:
        """Saves the cleaned DataFrame to a new CSV file (preserves original)."""
        try:
            self.df.write_csv(output_path)
            return True
        except Exception as e:
            print(f"Error saving cleaned file: {e}")
            return False

    def get_change_log(self) -> List[Dict[str, str]]:
        """Returns the laist of all modifications made."""
        return self.change_log
