"""Regression coverage for typed, null-aware vectorized change logging."""
from datetime import date, datetime, time
import unittest

import polars as pl

from src.tools.cleaning_engine import CleaningEngine


class LineageTypeChangeTests(unittest.TestCase):
    def engine(self, values, dtype=None):
        engine = CleaningEngine("unused.csv")
        engine.df = pl.DataFrame({"value": pl.Series("value", values, dtype=dtype)})
        return engine

    def test_parse_date_rejects_invalid_values_without_mutating_source(self):
        engine = self.engine(["2025-01-01", " 2025-02-01 ", "invalid", None])
        original = engine.df.clone()
        with self.assertRaisesRegex(ValueError, 'Conversion blocked'):
            engine.parse_date('value','%Y-%m-%d')
        self.assertTrue(engine.df.equals(original))
        self.assertEqual(engine.get_change_log(),[])
        engine = self.engine(["2025-01-01", " 2025-02-01 ", None])
        self.assertEqual(engine.parse_date('value','%Y-%m-%d'),1)
        self.assertEqual(engine.df['value'].to_list(),[date(2025,1,1),date(2025,2,1),None])
        self.assertEqual(engine.get_change_log()[0]['old_value'],' 2025-02-01 ')

    def test_date_null_fill_logs_only_filled_row(self):
        engine = self.engine([date(2025, 1, 1), None], pl.Date)
        self.assertEqual(engine.fill_nulls("value", "constant", date(2025, 1, 2)), 1)
        self.assertEqual(engine.get_change_log()[0]["row_index"], "1")
        self.assertEqual(engine.df["value"].dtype, pl.Date)

    def test_string_to_datetime_and_time_lineage(self):
        for dtype, values in [(pl.Datetime, [datetime(2025, 1, 1), None]),
                              (pl.Time, [time(12, 30), None])]:
            with self.subTest(dtype=dtype):
                after = pl.Series("value", values, dtype=dtype)
                before = after.cast(pl.Utf8)
                engine = self.engine(values, dtype)
                self.assertEqual(engine._log_column_changes("parse", "value", before), 0)

    def test_numeric_conversion_rejects_invalid_or_formatted_values(self):
        engine = self.engine(["1", "invalid", None, " 2,000 "])
        original = engine.df.clone()
        with self.assertRaisesRegex(ValueError, 'Conversion blocked'):
            engine.coerce_numeric('value')
        self.assertTrue(engine.df.equals(original))
        self.assertEqual(engine.get_change_log(),[])

    def test_numeric_promotion_does_not_log_unchanged_values(self):
        engine = self.engine([1, None, 4], pl.Int64)
        self.assertEqual(engine.fill_nulls("value", "mean"), 1)
        self.assertEqual(engine.df["value"].to_list(), [1.0, 2.5, 4.0])
        self.assertEqual([r["row_index"] for r in engine.get_change_log()], ["1"])

    def test_string_to_null_and_null_to_value(self):
        engine = self.engine(["null", None, "Alice"])
        self.assertEqual(engine.standardize_null_tokens("value"), 1)
        self.assertEqual(engine.fill_nulls("value", "constant", "Unknown"), 2)
        self.assertEqual(engine.df["value"].to_list(), ["Unknown", "Unknown", "Alice"])

    def test_same_type_trim_and_unchanged_nan(self):
        engine = self.engine([" Alice ", "Bob", None])
        self.assertEqual(engine.trim_whitespace(["value"]), 1)
        numeric = self.engine([float("nan"), 1.0, None], pl.Float64)
        self.assertEqual(numeric.fill_nulls("value", "constant", 0), 1)
        self.assertEqual([r["row_index"] for r in numeric.get_change_log()], ["2"])

    def test_empty_string_column_can_be_parsed_with_explicit_format(self):
        engine = self.engine([], pl.Utf8)
        self.assertEqual(engine.parse_date("value", "%Y-%m-%d"), 0)
        self.assertEqual(engine.df["value"].dtype, pl.Date)


if __name__ == "__main__":
    unittest.main()
