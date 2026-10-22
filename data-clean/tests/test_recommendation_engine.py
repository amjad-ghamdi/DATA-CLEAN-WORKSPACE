from src.agent.recommendation_engine import RecommendationEngine


def test_context_aware_series_recommendation():
    engine = RecommendationEngine()
    result = engine.recommend_missing(
        column="number_of_episodes",
        dtype="Float64",
        missing_info={"percentage_value": 70.0},
        column_profile={"unique_ratio": 0.2},
        all_columns={"content_type", "number_of_episodes"},
        protected_columns=set(),
    )

    assert result["strategy"] == "contextual_median"
    assert result["auto_eligible"] is True
    assert result["context_column"] == "content_type"
    assert result["round_result"] is True


def test_high_missing_numeric_defaults_to_keep():
    engine = RecommendationEngine()
    result = engine.recommend_missing(
        column="production_budget",
        dtype="Float64",
        missing_info={"percentage_value": 65.0},
        column_profile={"unique_ratio": 0.5},
        all_columns={"production_budget"},
        protected_columns=set(),
    )

    assert result["strategy"] == "keep"
    assert result["auto_eligible"] is False


def test_low_missing_numeric_recommends_median():
    engine = RecommendationEngine()
    result = engine.recommend_missing(
        column="imdb_rating",
        dtype="Float64",
        missing_info={"percentage_value": 14.0},
        column_profile={"unique_ratio": 0.1},
        all_columns={"imdb_rating"},
        protected_columns=set(),
    )

    assert result["strategy"] == "median"
    assert result["auto_eligible"] is True
