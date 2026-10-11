import polars as pl
from pathlib import Path
from typing import Dict, Any, List, Union, Tuple

class CleaningEngine:
    \"\"\"
    Advanced Backend tool for performing data cleaning operations using Polars.
    Implements a full Lineage system to track every single cell change.
    \"\"\"
    def __init__(self, file_path: str):
        self.file_path = Path(file_path)
        self.df = None
        # Lineage: Store changes as a list of dicts for later conversion to DataFrame
        self.change_log = []

    def load_data(self) -> bool:
        \"\"\"Loads the dataset into memory.\"\"\"
        try:
            if self.file_path.suffix.lower() == '.csv':
                self.df = pl.read_csv(self.file_path)
                return True
            return False
        except Exception as e:
            print(f\"Error loading file for cleaning: {e}\")
            return False

    def _log_change(self, action: str, column: str, row_idx: int, old_val: Any, new_val: Any):
        \"\"\"Records a precise cell-level change for full lineage transparency.\"\"\"
        if old_val == new_val:
            return
        self.change_log.append({
            \"action\": action,
            \"column\": column,
            \"row_index\": row_idx,
            \"old_value\": old_val,
            \"new_value\": new_val
        })

    def trim_whitespace(self, columns: List[str]) -> None:
        \"\"\"Removes leading/trailing whitespace with cell-level lineage. (SAFE)\"\"\"
        for col in columns:
            if self.df[col].dtype == pl.Utf8:
                # Track changes before applying
                current_vals = self.df[col].to_list()
                self.df = self.df.with_columns(pl.col(col).str.strip_chars())
                new_vals = self.df[col].to_list()
                
                for i, (old, new) in enumerate(zip(current_vals, new_vals)):
                    self._log_change(\"trim_whitespace\", col, i, old, new)

    def normalize_casing(self, column: str, target_case: str = \"lower\") -> None:
        \"\"\"Normalizes text casing with cell-level lineage. (REVIEW)\"\"\"
        if self.df[column].dtype == pl.Utf8:
            current_vals = self.df[column].to_list()
            if target_case == \"lower\":
                self.df = self.df.with_columns(pl.col(column).str.to_lowercase())
            elif target_case == \"upper\":
                self.df = self.df.with_columns(pl.col(column).str.to_uppercase())
            elif target_case == \"title\":
                self.df = self.df.with_columns(pl.col(column).str.to_titlecase())
            
            new_vals = self.df[column].to_list()
            for i, (old, new) in enumerate(zip(current_vals, new_vals)):
                self._log_change(\"normalize_casing\", column, i, old, new)

    def fill_nulls(self, column: str, strategy: str, fill_value: Any = None) -> None:
        \"\"\"Fills missing values with lineage tracking.\"\"\"
        if self.df is None: return

        current_vals = self.df[column].to_list()
        
        if strategy == \"constant\":
            self.df = self.df.with_columns(pl.col(column).fill_null(fill_value))
        elif strategy == \"mean\":
            mean_val = self.df[column].mean()
            self.df = self.df.with_columns(pl.col(column).fill_null(mean_val))
        elif strategy == \"median\":
            median_val = self.df[column].median()
            self.df = self.df.with_columns(pl.col(column).fill_null(median_val))
        elif strategy == \"mode\":
            mode_res = self.df[column].mode()
            mode_val = mode_res[0] if mode_res.len() > 0 else None
            self.df = self.df.with_columns(pl.col(column).fill_null(mode_val))
        elif strategy == \"drop\":
            self.df = self.df.drop_nulls(subset=[column])
            self._log_change(\"fill_nulls\", column, \"N/A\", \"multiple rows\", \"dropped\")
            return # Drop changes indices, so we return early

        new_vals = self.df[column].to_list()
        for i, (old, new) in enumerate(zip(current_vals, new_vals)):
            self._log_change(\"fill_nulls\", column, i, old, new)

    def drop_duplicates(self, subset: List[str] = None) -> None:
        \"\"\"Removes duplicate rows (HIGH IMPACT).\"\"\"
        initial_count = self.df.height
        self.df = self.df.unique(subset=subset)
        final_count = self.df.height
        self._log_change(\"drop_duplicates\", \"dataset\", \"N/A\", f\"{initial_count} rows\", f\"{final_count} rows\")

    def save_cleaned_data(self, output_path: str) -> bool:
        \"\"\"Saves the cleaned DataFrame to CSV.\"\"\"
        try:
            self.df.write_csv(output_path)
            return True
        except Exception as e:
            print(f\"Error saving cleaned file: {e}\")
            return False

    def get_lineage_df(self) -> pl.DataFrame:
        \"\"\"Converts the change log to a Polars DataFrame for analysis.\"\"\"
        return pl.DataFrame(self.change_log)
