import sqlite3
import tempfile
import unittest
from pathlib import Path

import scraper
from scripts.db_summary import build_summary


def record(ticker: str, rate: float) -> dict:
    return {
        "currency": ticker,
        "ticker": ticker,
        "tt_buy": rate,
        "tt_sell": rate + 1,
        "bill_buy": None,
        "bill_sell": None,
        "ftc_buy": None,
        "ftc_sell": None,
        "cn_buy": None,
        "cn_sell": None,
        "date": "2026-07-24",
        "category": "BETWEEN_10_20",
    }


class DatabaseSummaryTest(unittest.TestCase):
    def test_reports_added_updated_and_removed_records(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            before = Path(temp_dir) / "before.db"
            after = Path(temp_dir) / "after.db"

            with sqlite3.connect(before) as conn:
                scraper.init_db(conn)
                scraper.upsert(conn, [record("USD", 86.0), record("EUR", 100.0)])
            with sqlite3.connect(after) as conn:
                scraper.init_db(conn)
                scraper.upsert(conn, [record("USD", 87.0), record("GBP", 115.0)])

            summary = build_summary(after, before, "success")

            self.assertIn("| 1 | 1 | 1 |", summary)
            self.assertIn("| Rows | 2 |", summary)
            self.assertIn("| Scraper | ✅ Success |", summary)
            self.assertIn("| Integrity | ✅ `ok` |", summary)


if __name__ == "__main__":
    unittest.main()