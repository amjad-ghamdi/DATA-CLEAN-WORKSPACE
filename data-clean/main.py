from src.agent.coordinator import coordinator
from src.agent.registry import registry
from src.skills.data_quality_skill import run_data_quality_analysis
from src.skills.safe_cleaning_skill import run_cleaning_plan


def initialize_agent() -> None:
    registry.register_skill(
        name="assess_quality",
        func=run_data_quality_analysis,
        description="Profiles quality, privacy, schema, missing values, duplicates, outliers, and transform candidates.",
    )
    registry.register_skill(
        name="plan_cleaning",
        func=run_cleaning_plan,
        description="Builds a SAFE / REVIEW / HIGH IMPACT cleaning and transformation plan without modifying data.",
    )


def main() -> None:
    initialize_agent()
    print(coordinator.execute("analyze quality and plan cleaning"))


if __name__ == "__main__":
    main()
