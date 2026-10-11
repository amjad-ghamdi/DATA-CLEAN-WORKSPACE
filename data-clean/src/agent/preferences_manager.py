import json
from pathlib import Path
from typing import Dict, Any, List, Optional

class PreferencesManager:
    \"\"\"
    Manages user preferences for data cleaning operations.
    Allows the agent to learn from user decisions to reduce future friction.
    \"\"\"
    def __init__(self, prefs_path: str = \"data-clean/user_preferences.json\"):
        self.prefs_path = Path(prefs_path)
        self.preferences = self._load_preferences()

    def _load_preferences(self) -> Dict[str, Any]:
        \"\"\"Loads preferences from JSON file.\"\"\"
        if self.prefs_path.exists():
            try:
                with open(self.prefs_path, 'r') as f:
                    return json.load(f)
            except Exception as e:
                print(f\"Error loading preferences: {e}\")
        return {\"risk_overrides\": {}, \"trusted_columns\": []}

    def save_preferences(self) -> None:
        \"\"\"Saves current preferences to JSON file.\"\"\"
        try:
            with open(self.prefs_path, 'w') as f:
                json.dump(self.preferences, f, indent=4)
        except Exception as e:
            print(f\"Error saving preferences: {e}\")

    def set_risk_override(self, action: str, column: str, new_risk: str) -> None:
        \"\"\"
        Updates the risk level for a specific action on a specific column.
        Example: 'drop_duplicates' on 'customer_id' changed from 'HIGH' to 'SAFE'.
        \"\"\"
        if \"risk_overrides\" not in self.preferences:
            self.preferences[\"risk_overrides\"] = {}
        
        # Create a unique key for the action-column pair
        key = f\"{action}:{column}\"
        self.preferences[\"risk_overrides\"][key] = new_risk
        self.save_preferences()

    def get_risk_level(self, action: str, column: str, default_risk: str) -> str:
        \"\"\"Retrieves the risk level, checking for user overrides first.\"\"\"
        key = f\"{action}:{column}\"
        return self.preferences.get(\"risk_overrides\", {}).get(key, default_risk)

    def add_trusted_column(self, column: str) -> None:
        \"\"\"Marks a column as trusted, reducing the risk of all operations on it.\"\"\"
        if column not in self.preferences[\"trusted_columns\"]:
            self.preferences[\"trusted_columns\"].append(column)
            self.save_preferences()

    def is_column_trusted(self, column: str) -> bool:
        \"\"\"Checks if a column is marked as trusted.\"\"\"
        return column in self.preferences.get(\"trusted_columns\", [])
