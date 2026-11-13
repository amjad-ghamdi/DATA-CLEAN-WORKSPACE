"""Run the analyze/clean route handlers against a real CSV and real engine."""
from io import BytesIO
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi import UploadFile
import polars as pl

import main_api as api


class CleaningLineageApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_clean_dates_after_duplicate_and_missing_row_removal(self):
        contents = (
            b"title,genre,rating,release_date\n"
            b"A,Drama,8,2025-01-01\n"
            b"B,,7,2025-02-01\n"
            b"C,Comedy,,invalid\n"
            b"D,Drama,6,\n"
            b"A,Drama,8,2025-01-01\n"
        )
        with tempfile.TemporaryDirectory() as directory, patch.object(api, "UPLOAD_DIR", Path(directory)):
            analysis = await api.analyze_data(UploadFile(filename="movies.csv", file=BytesIO(contents)))
            self.assertEqual(analysis["status"], "success")
            request = api.CleaningRequest(filename=analysis["filename"], actions=[
                api.CleaningAction(action="drop_duplicates", column="dataset"),
                api.CleaningAction(action="fill_nulls", column="genre", strategy="drop"),
                api.CleaningAction(action="fill_nulls", column="rating", strategy="median"),
                api.CleaningAction(action="parse_date", column="release_date", date_format="%Y-%m-%d"),
            ])
            result = await api.clean_data(request)
            self.assertEqual(result["status"], "success")
            self.assertEqual(result["validation"]["rows_before"], 5)
            self.assertEqual(result["validation"]["rows_after"], 3)
            self.assertEqual(result["after_profile"]["schema"]["release_date"], "Date")
            self.assertEqual(result["preview"][1]["rating"], 7.0)
            self.assertIsNone(result["preview"][1]["release_date"])
            for url_key in ["download_url", "report_download_url", "lineage_download_url"]:
                self.assertTrue((Path(directory) / result[url_key].split("/")[-1]).is_file())
            log = pl.read_csv(Path(directory) / result["lineage_download_url"].split("/")[-1])
            date_rows = log.filter(pl.col("action") == "parse_date")
            self.assertEqual(date_rows.height, 1)
            self.assertEqual(date_rows["old_value"].to_list(), ["invalid"])


if __name__ == "__main__":
    unittest.main()
