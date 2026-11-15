from src.recommendations import RecommendationEngine


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


def test_cli_quality_and_plan_are_read_only(tmp_path, monkeypatch, capsys):
    import main

    source = tmp_path / "sample.csv"
    original = b"name,score\n Alice ,1\nBob,2\nBob,2\n"
    source.write_bytes(original)
    for task, expected in [("quality", "DATA CLEAN - QUALITY PROFILE"),
                           ("plan", "DATA CLEAN - RECOMMENDED PLAN")]:
        monkeypatch.setattr("sys.argv", ["main.py", str(source), "--task", task])
        main.main()
        assert expected in capsys.readouterr().out
        assert source.read_bytes() == original
