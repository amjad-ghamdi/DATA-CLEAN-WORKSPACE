import polars as pl

from main_api import _clean_output_extension, _lightweight_profile
from src.tools.cleaning_engine import CleaningEngine


def test_large_dataset_export_defaults_to_csv():
    assert _clean_output_extension("source.xlsx", 200_000) == ".csv"
    assert _clean_output_extension("source.xlsx", 199_999) == ".xlsx"
    assert _clean_output_extension("source.csv", 500_000) == ".csv"


def test_lightweight_profile_keeps_exact_core_metrics():
    df = pl.DataFrame(
        {
            "id": [1, 2, 2],
            "value": [10, None, None],
        }
    )

    profile = _lightweight_profile(df)

    assert profile["duplicates"]["total_rows"] == 3
    assert profile["duplicates"]["duplicate_rows"] == 0
    assert profile["missing_values"]["value"]["count"] == 2
    assert profile["schema"]["id"].startswith("Int")


def test_vectorized_lineage_records_only_changed_rows():
    engine = CleaningEngine("unused.csv")
    engine.df = pl.DataFrame(
        {
            "name": [" Alice ", "Bob", " Carol "],
        }
    )

    changed = engine.trim_whitespace(["name"])

    assert changed == 2
    assert len(engine.get_change_log()) == 2
    assert engine.df["name"].to_list() == ["Alice", "Bob", "Carol"]


def test_vectorized_winsorize_lineage_records_affected_rows():
    engine = CleaningEngine("unused.csv")
    engine.df = pl.DataFrame(
        {
            "value": [1, 2, 100, 3],
        }
    )

    changed = engine.handle_outliers(
        "value",
        "winsorize",
        0,
        10,
    )

    assert changed == 1
    assert len(engine.get_change_log()) == 1
    assert engine.df["value"].to_list() == [1, 2, 10, 3]
