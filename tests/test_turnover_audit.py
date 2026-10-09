import sys
import unittest
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from audit_turnover import reconcile, same_turnover


class TurnoverAuditTests(unittest.TestCase):
    def test_units_and_unsorted_intraday_bars(self):
        daily = pd.DataFrame(
            {
                "datetime": pd.to_datetime(["2026-01-05"]),
                "amount": [1232361.7488],
                "volume": [3941755.65],
            }
        )
        minute = pd.DataFrame(
            {
                "datetime": pd.to_datetime(
                    ["2026-01-05 15:00", "2026-01-05 10:30", "2026-01-05 14:00", "2026-01-05 11:30"]
                ),
                "amount": [403953715, 394519038, 163328309, 270550798],
                "volume": [1284852, 1269422, 521445, 866037],
            }
        )
        row = reconcile(daily, minute).iloc[0]
        self.assertEqual(row.amount_60min_yuan, 1232351860)
        self.assertAlmostEqual(row.amount_diff_yuan, -9888.8, places=4)
        self.assertAlmostEqual(row.amount_diff_pct, -0.0008024284, places=8)
        self.assertEqual(row.bars, 4)

    def test_missing_dates_are_not_zero_and_zero_denominator_is_null(self):
        daily = pd.DataFrame(
            {
                "datetime": pd.to_datetime(["2026-01-05", "2026-01-06"]),
                "amount": [1.0, 0.0],
                "volume": [1.0, 0.0],
            }
        )
        minute = pd.DataFrame(
            {
                "datetime": pd.to_datetime(["2026-01-06 15:00", "2026-01-07 15:00"]),
                "amount": [0, 1000],
                "volume": [0, 1],
            }
        )
        result = reconcile(daily, minute)
        self.assertTrue(pd.isna(result.iloc[0].amount_60min_yuan))
        self.assertTrue(pd.isna(result.iloc[1].amount_diff_pct))
        self.assertTrue(pd.isna(result.iloc[2].amount_daily_yuan))

    def test_duplicate_keys_fail_instead_of_double_counting(self):
        daily = pd.DataFrame(
            {"datetime": pd.to_datetime(["2026-01-05"]), "amount": [1.0], "volume": [1.0]}
        )
        minute = pd.DataFrame(
            {
                "datetime": pd.to_datetime(["2026-01-05 15:00"] * 2),
                "amount": [500, 500],
                "volume": [1, 1],
            }
        )
        with self.assertRaisesRegex(ValueError, "Duplicate minute"):
            reconcile(daily, minute)
        with self.assertRaisesRegex(ValueError, "Duplicate daily"):
            reconcile(pd.concat([daily, daily]), minute.iloc[:1])

    def test_partial_null_is_not_a_complete_day_total(self):
        daily = pd.DataFrame(
            {"datetime": pd.to_datetime(["2026-01-05"]), "amount": [1.0], "volume": [1.0]}
        )
        minute = pd.DataFrame(
            {
                "datetime": pd.to_datetime(["2026-01-05 10:30", "2026-01-05 15:00"]),
                "amount": [500, float("nan")],
                "volume": [1, 0],
            }
        )
        row = reconcile(daily, minute).iloc[0]
        self.assertTrue(pd.isna(row.amount_60min_yuan))
        self.assertEqual(row.bars, 2)

    def test_source_comparison_ignores_timestamp_resolution_only(self):
        left = pd.DataFrame(
            {
                "datetime": pd.to_datetime(["2026-01-05"]).astype("datetime64[ms]"),
                "amount": [1.0],
                "volume": [2.0],
            }
        )
        right = left.copy()
        right["datetime"] = right.datetime.astype("datetime64[us]")
        self.assertTrue(same_turnover(left, right))
        right.loc[0, "amount"] = 1.001
        self.assertFalse(same_turnover(left, right))


if __name__ == "__main__":
    unittest.main()
