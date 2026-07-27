import datetime
import sqlite3
import unittest

import scraper


class ParsingHelpersTest(unittest.TestCase):
    def test_parse_date_accepts_supported_separators(self) -> None:
        expected = datetime.date(2026, 7, 24)
        self.assertEqual(scraper.parse_date("Date: 24-07-2026"), expected)
        self.assertEqual(scraper.parse_date("Date: 24/07/2026"), expected)
        self.assertIsNone(scraper.parse_date("Date: 32-07-2026"))

    def test_categorize_handles_wording_drift(self) -> None:
        self.assertEqual(scraper.categorize("transactions below 10 lacs"), "BELOW_10")
        self.assertEqual(
            scraper.categorize("transactions between 10 and 20 lakhs"),
            "BETWEEN_10_20",
        )
        self.assertIsNone(scraper.categorize("FOREX CARD RATES"))

    def test_parse_row_anchors_on_ticker_and_ignores_trailing_column(self) -> None:
        row = [
            None,
            "United States\nDollar",
            "USD/INR",
            "86.10",
            "87.20",
            "85.90",
            "87.40",
            "-",
            "-",
            "85.00",
            "88.00",
            "84.50",
        ]

        self.assertEqual(
            scraper.parse_row(row),
            {
                "currency": "United States Dollar",
                "ticker": "USD",
                "tt_buy": 86.1,
                "tt_sell": 87.2,
                "bill_buy": 85.9,
                "bill_sell": 87.4,
                "ftc_buy": None,
                "ftc_sell": None,
                "cn_buy": 85.0,
                "cn_sell": 88.0,
            },
        )


class DatabaseTest(unittest.TestCase):
    def test_init_and_upsert_are_idempotent(self) -> None:
        record = {
            "currency": "United States Dollar",
            "ticker": "USD",
            "tt_buy": 86.1,
            "tt_sell": 87.2,
            "bill_buy": None,
            "bill_sell": None,
            "ftc_buy": None,
            "ftc_sell": None,
            "cn_buy": None,
            "cn_sell": None,
            "date": "2026-07-24",
            "category": "BETWEEN_10_20",
        }
        with sqlite3.connect(":memory:") as conn:
            scraper.init_db(conn)
            scraper.upsert(conn, [record])
            updated = {**record, "tt_buy": 86.2}
            scraper.upsert(conn, [updated])

            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM forex_rates").fetchone()[0], 1
            )
            self.assertEqual(
                conn.execute("SELECT tt_buy FROM forex_rates").fetchone()[0], 86.2
            )
            indexes = {
                row[1] for row in conn.execute("PRAGMA index_list(forex_rates)")
            }
            self.assertIn("ix_forex_rates_date", indexes)


if __name__ == "__main__":
    unittest.main()
