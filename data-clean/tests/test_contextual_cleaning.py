import polars as pl

from src.tools.cleaning_engine import CleaningEngine


def test_contextual_fill_only_changes_series_rows(tmp_path):
    path = tmp_path / "content.csv"
    pl.DataFrame(
        {
            "content_type": ["Movie", "TV Series", "TV Series", "Movie", "Limited Series"],
            "number_of_episodes": [None, 10.0, None, None, 20.0],
        }
    ).write_csv(path)

    engine = CleaningEngine(str(path))
    assert engine.load_data() is True

    changed = engine.contextual_fill(
        "number_of_episodes",
        "content_type",
        ["series", "tv"],
        statistic="median",
        round_result=True,
    )

    values = engine.df["number_of_episodes"].to_list()
    assert changed == 1
    assert values[0] is None
    assert values[2] == 15.0
    assert values[3] is None


def test_keep_outliers_makes_no_change(tmp_path):
    path = tmp_path / "values.csv"
    pl.DataFrame({"value": [1.0, 2.0, 100.0]}).write_csv(path)

    engine = CleaningEngine(str(path))
    assert engine.load_data() is True

    changed = engine.handle_outliers("value", "keep", 0.0, 10.0)
    assert changed == 0
    assert engine.df["value"].to_list() == [1.0, 2.0, 100.0]
