"""Unit tests for the pure helpers in server.py (no HTTP involved)."""

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
LEDGER_DIR = os.path.dirname(HERE)
sys.path.insert(0, LEDGER_DIR)

import server  # noqa: E402


class ParseDateTests(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(server.parse_date("2026-03-05").isoformat(), "2026-03-05")
        self.assertEqual(server.parse_date("2024-02-29").isoformat(), "2024-02-29")  # leap year

    def test_invalid(self):
        for bad in ("2026-02-30", "2026-13-01", "2026-00-10", "2026-3-5",
                    "05-03-2026", "2026-03-05x", "", None, 20260305, "2026-03-05 "):
            self.assertIsNone(server.parse_date(bad), repr(bad))


class ParseMonthTests(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(server.parse_month("2026-03"), (2026, 3))
        self.assertEqual(server.parse_month("2026-12"), (2026, 12))

    def test_invalid(self):
        for bad in ("2026-13", "2026-00", "2026-3", "202603", "2026-03-01",
                    "", None, 202603):
            self.assertIsNone(server.parse_month(bad), repr(bad))


class ParseAmountTests(unittest.TestCase):
    def test_strings(self):
        self.assertEqual(server.parse_amount("12.50"), (1250, None))
        self.assertEqual(server.parse_amount("0.01"), (1, None))
        self.assertEqual(server.parse_amount("100"), (10000, None))
        self.assertEqual(server.parse_amount("  3.00  "), (300, None))

    def test_numbers(self):
        self.assertEqual(server.parse_amount(12.5), (1250, None))
        self.assertEqual(server.parse_amount(12), (1200, None))
        self.assertEqual(server.parse_amount(0.1), (10, None))

    def test_scientific_notation_is_a_valid_decimal(self):
        self.assertEqual(server.parse_amount("1e2"), (10000, None))

    def test_rejected(self):
        for bad in ("0", "-1.00", "1.234", "abc", "", None, True, False,
                    "12.50.00", "NaN", "Infinity"):
            cents, err = server.parse_amount(bad)
            self.assertIsNone(cents, repr(bad))
            self.assertIsNotNone(err, repr(bad))


if __name__ == "__main__":
    unittest.main()
