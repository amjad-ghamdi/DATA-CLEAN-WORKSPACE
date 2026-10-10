from typing import Any, List, Dict
from .registry import registry
from .planner import Planner

class Coordinator:
    """
    The Coordinator is the main orchestrator of the Data Clean agent.
    It manages the flow from user input to final result by coordinating 
    between the Planner and the Registry.
    """
    def __init__(self):
        # Inject the global registry into the planner
        self.planner = Planner(registry=registry)
        self.registry = registry

    def execute(self, user_query: str) -> str:
        """
        Main execution flow:
        1. Plan: Transform query into a set of tasks.
        2. Execute: Run each task using the registry.
        3. Finalize: Collect and format results.
        """
        print(f"Coordinator: Analyzing request -> {user_query}")
        
        # Step 1: Planning
        plan = self.planner.plan(user_query)
        print(f"Coordinator: Plan generated with {len(plan)} steps.")

        final_results = []
        
        # Step 2: Execution
        for step in plan:
            step_num = step['step']
            action_name = step['action']
            action_type = step['type']
            
            print(f"Coordinator: Executing Step {step_num} [{action_type}] -> {action_name}...")
            
            try:
                if action_type == "skill":
                    func = self.registry.get_skill(action_name)
                elif action_type == "tool":
                    func = self.registry.get_tool(action_name)
                else:
                    raise ValueError(f"Unknown action type: {action_type}")
                
                # Execute the function and store the result
                result = func()
                final_results.append(f"Step {step_num} ({action_name}): {result}")
                
            except Exception as e:
                error_msg = f"Step {step_num} ({action_name}) failed: {str(e)}"
                print(f"Error: {error_msg}")
                final_results.append(error_msg)

        # Step 3: Finalization
        return "\n".join(final_results)

# Global instance for the project
coordinator = Coordinator()
