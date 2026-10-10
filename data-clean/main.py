from data_clean.src.agent.coordinator import coordinator
from data_clean.src.agent.registry import registry
from data_clean.src.skills.data_quality_skill import run_data_quality_analysis

def initialize_agent():
    """
    Registers all available skills and tools into the global registry.
    This must be called before the coordinator executes any requests.
    """
    print("Initializing Data Clean Agent...")
    
    # Register the Data Quality Skill
    # Note: 'assess_quality' is the action name the Planner looks for
    registry.register_skill(
        name="assess_quality", 
        func=run_data_quality_analysis, 
        description="Analyzes data quality, missing values, and duplicates in a dataset."
    )
    
    print("Agent initialized successfully. All skills registered.\n")

def main():
    # 1. Setup
    initialize_agent()
    
    # 2. Test Queries
    test_queries = [
        "أريد فحص جودة البيانات في الملف",
        "Check the data quality please",
        "هل هناك قيم مفقودة في البيانات؟"
    ]
    
    for query in test_queries:
        print(f"\nUser Query: {query}")
        print("-" * 30)
        result = coordinator.execute(query)
        print(f"\nFinal Result:\n{result}")
        print("-" * 30)

if __name__ == "__main__":
    main()
