import polars as pl

from src.tools.integrity_checker import IntegrityChecker
from src.tools.quality_analyzer import QualityAnalyzer


def test_large_dataset_mode_uses_sample_for_expensive_checks():
    df = pl.DataFrame(
        {
            "id": [f"id-{i}" for i in range(20)],
            "category": ["A", "B"] * 10,
            "value": list(range(20)),
        }
    )

    analyzer = QualityAnalyzer(
        "unused.csv",
        dataframe=df,
        large_dataset_threshold=10,
        sample_size=5,
    )
    profile = analyzer.run_full_profile()

    assert profile["processing"]["mode"] == "large_dataset"
    assert profile["processing"]["sample_rows"] == 5
    assert profile["processing"]["expensive_checks_sampled"] is True
    assert profile["duplicates"]["total_rows"] == 20
    assert profile["missing_values"]["value"]["count"] == 0


def test_preloaded_dataframe_is_shared_without_source_file_read():
    df = pl.DataFrame(
        {
            "id": [1, 2, 3],
            "value": [10, 20, 30],
        }
    )

    analyzer = QualityAnalyzer(
        "file-does-not-exist.csv",
        dataframe=df,
    )
    profile = analyzer.run_full_profile()

    checker = IntegrityChecker(
        "file-does-not-exist.csv",
        dataframe=df,
    )
    integrity = checker.get_integrity_report()

    assert "error" not in profile
    assert profile["duplicates"]["total_rows"] == 3
    assert integrity["detected_primary_keys"] == ["id"]


def test_hash_duplicate_count_matches_extra_duplicate_rows():
    df = pl.DataFrame(
        {
            "a": [1, 1, 2, 3, 3],
            "b": ["x", "x", "y", "z", "z"],
        }
    )

    profile = QualityAnalyzer(
        "unused.csv",
        dataframe=df,
    ).run_full_profile()

    assert profile["duplicates"]["total_rows"] == 5
    assert profile["duplicates"]["unique_rows"] == 3
    assert profile["duplicates"]["duplicate_rows"] == 2
