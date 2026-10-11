import polars as pl
from pathlib import Path
from typing import Dict, Any, List, Set, Tuple

class IntegrityChecker:
    \"\"\"
    Advanced tool to ensure data integrity and prevent breaking relationships 
    between datasets during cleaning.
    \"\"\"
    def __init__(self, file_path: str):
        self.file_path = Path(file_path)
        self.df = None
        # Potential identifiers based on common naming conventions
        self.id_keywords = {"id", "uuid", "key", "code", "pk", "fk", "reference"}

    def load_data(self) -> bool:
        \"\"\"Loads the CSV file into a Polars DataFrame.\"\"\"
        try:
            if self.file_path.suffix.lower() == '.csv':
                self.df = pl.read_csv(self.file_path)
                return True
            return False
        except Exception as e:
            print(f\"Error loading file for integrity check: {e}\")
            return False

    def detect_primary_keys(self) -> List[str]:
        \"\"\"
        Heuristically detects columns that likely act as primary keys 
        based on naming and uniqueness.
        \"\"\"
        if self.df is None: return []
        
        potential_keys = []
        for col in self.df.columns:
            col_lower = col.lower()
            # Rule 1: Name contains ID keywords
            name_match = any(kw in col_lower for kw in self.id_keywords)
            # Rule 2: Value is unique across the whole column
            is_unique = self.df[col].n_unique() == self.df.height
            
            if name_match and is_unique:
                potential_keys.append(col)
                
        return potential_keys

    def check_critical_impact(self, column: str, action: str) -> bool:
        \"\"\"
        Determines if a cleaning action on a specific column is CRITICAL.
        Actions like 'drop_duplicates' or 'fill_nulls' on a Primary Key are critical.
        \"\"\"
        if self.df is None: return False
        
        keys = self.detect_primary_keys()
        if column in keys:
            # Any modification to a unique identifier is considered critical
            return True
        
        # Specific critical actions regardless of column (e.g., dataset-wide drops)
        if action == \"drop_duplicates\" and column == \"dataset\":
            return True
            
        return False

    def verify_relationship_stability(self, column: str, old_values: List[Any], new_values: List[Any]) -> bool:
        \"\"\"
        Verifies if changing values in a column breaks its uniqueness or 
        introduces collisions that could break foreign key relationships.
        \"\"\"
        if self.df is None: return True
        
        # If the column was unique, it MUST remain unique after cleaning
        was_unique = self.df[column].n_unique() == self.df.height
        if was_unique:
            if len(set(new_values)) != len(new_values):
                return False # Collision detected! Integrity broken.
                
        return True

    def get_integrity_report(self) -> Dict[str, Any]:
        \"\"\"Generates a full integrity profile of the dataset.\"\"\"
        if not self.load_data():
            return {\"error\": \"Data not loaded\"}
            
        keys = self.detect_primary_keys()
        return {
            \"detected_primary_keys\": keys,
            \"total_columns\": len(self.df.columns),
            \"integrity_status\": \"Stable\" if keys else \"Warning: No unique identifiers found\",
            \"critical_columns_count\": len(keys)
        }
