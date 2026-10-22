import polars as pl

from src.tools.tabular_io import (
    internal_cache_path,
    preferred_processing_source,
    read_tabular,
    write_internal_cache,
)


def test_internal_parquet_cache_round_trip(tmp_path):
    source = tmp_path / "large.xlsx"
    source.write_bytes(b"placeholder")

    df = pl.DataFrame(
        {
            "a": [1, 2],
            "b": ["x", "y"],
        }
    )

    cache_path = write_internal_cache(df, source)

    assert cache_path == internal_cache_path(source)
    assert preferred_processing_source(source) == cache_path
    assert read_tabular(cache_path).to_dicts() == df.to_dicts()
