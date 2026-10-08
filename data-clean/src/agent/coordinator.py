import polars as pl
from pathlib import Path
from typing import Dict, Any, Optional
from src.tools.profiler import DataProfiler

class DataCleanCoordinator:
    """
    The Central Coordinator for the DATA CLEAN Agent.
    Its role is to bridge the gap between Skills (Logic) and Tools (Execution).
    """
    def __init__(self):
        self.current_file = None
        self.profile_results = None

    def set_active_file(self, file_path: str) -> bool:
        """Sets the file that the agent is currently working on."""
        path = Path(file_path)
        if path.exists() and path.suffix.lower() in ['.csv', '.xlsx', '.xls']:
            self.current_file = path
            return True
        return False

    def run_analysis_stage(self) -> Dict[str, Any]:
        """
        Executes the Data Analysis stage by invoking the Profiler tool.
        This corresponds to the 'dataclean-data-analysis' skill.
        """
        if not self.current_file:
            return {"error": "No active file selected for analysis."}
        
        try:
            # Initialize the tool
            profiler = DataProfiler(str(self.current_file))
            # Execute the analysis
            self.profile_results = profiler.profile()
            return self.profile_results
        except Exception as e:
            return {"error": f"Analysis failed: {str(e)}"}

    def get_current_state(self) -> Dict[str, Any]:
        """Returns the current state of the coordinator."""
        return {
            "active_file": str(self.current_file) if self.current_file else None,
            "has_profile": self.profile_results is not None,
            "last_stage": "analysis" if self.profile_results else "none"
        }

if __name__ == "__main__":
    # Local test
    coord = DataCleanCoordinator()
    print("Coordinator initialized. Ready to bridge Skills and Tools.")
