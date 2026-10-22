import pandas as pd
import polars as pl

from src.tools.tabular_io import output_extension_for, read_tabular, write_tabular


def test_xlsx_round_trip(tmp_path):
    source = tmp_path / "sample.xlsx"
    pd.DataFrame(
        {
            "name": ["A", "B"],
            "score": [1.0, None],
        }
    ).to_excel(source, index=False)

    df = read_tabular(source)
    assert df.height == 2
    assert df.columns == ["name", "score"]

    output = tmp_path / "cleaned_sample.xlsx"
    write_tabular(df, output)
    reread = read_tabular(output)
    assert reread.height == 2
    assert reread.columns == ["name", "score"]


def test_output_extension_preserves_csv_and_modernizes_excel():
    assert output_extension_for("sample.csv") == ".csv"
    assert output_extension_for("sample.xlsx") == ".xlsx"
    assert output_extension_for("sample.xls") == ".xlsx"
