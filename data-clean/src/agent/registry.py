from typing import Any, Dict, Callable, List

class ComponentRegistry:
    """
    Registry to manage available tools and skills for the Data Clean agent.
    It acts as a catalog that the Planner can query to find the right component for a task.
    """
    def __init__(self):
        self._tools: Dict[str, Dict[str, Any]] = {}
        self._skills: Dict[str, Dict[str, Any]] = {}

    def register_tool(self, name: str, func: Callable, description: str):
        """Registers a technical tool (e.g., a Polars function)."""
        self._tools[name] = {
            "func": func,
            "description": description
        }
        print(f"Tool '{name}' registered successfully.")

    def register_skill(self, name: str, func: Callable, description: str):
        """Registers a high-level skill (e.g., a data quality assessment flow)."""
        self._skills[name] = {
            "func": func,
            "description": description
        }
        print(f"Skill '{name}' registered successfully.")

    def get_tool(self, name: str) -> Callable:
        """Retrieves a tool function by its name."""
        if name not in self._tools:
            raise ValueError(f"Tool '{name}' not found in registry.")
        return self._tools[name]["func"]

    def get_skill(self, name: str) -> Callable:
        """Retrieves a skill function by its name."""
        if name not in self._skills:
            raise ValueError(f"Skill '{name}' not found in registry.")
        return self._skills[name]["func"]

    def list_available_tools(self) -> List[Dict[str, str]]:
        """Returns a list of all registered tools and their descriptions."""
        return [{"name": name, "description": info["description"]} for name, info in self._tools.items()]

    def list_available_skills(self) -> List[Dict[str, str]]:
        """Returns a list of all registered skills and their descriptions."""
        return [{"name": name, "description": info["description"]} for name, info in self._skills.items()]

# Global instance for the project
registry = ComponentRegistry()
