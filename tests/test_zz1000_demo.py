"""Regression checks for the publicly bundled daily research demo."""

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from examples.zz1000_daily_demo import DATA_DIR, load_sample, run_demo


class DailySampleTests(unittest.TestCase):
    def test_snapshot_has_events_and_normalized_units(self):
        bars, events = load_sample()
        self.assertEqual(len(bars), 2408)
        self.assertEqual(events.kind.tolist(), ["split", "dividend", "dividend"])
        self.assertTrue((bars.volume == bars.volume_source * 100).all())
        self.assertTrue((bars.amount == bars.amount_source * 1000).all())
        self.assertEqual(events.loc[events.kind == "split", "rounding"].iloc[0], "ceil")
        self.assertEqual(bars.index[0].strftime("%Y-%m-%d"), "2016-11-04")
        self.assertEqual(bars.index[-1].strftime("%Y-%m-%d"), "2026-09-30")

    def test_corrupted_snapshot_is_rejected_before_parquet_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "data"
            shutil.copytree(DATA_DIR, folder)
            with (folder / "512100.SH_daily.parquet").open("ab") as stream:
                stream.write(b"corruption")
            with self.assertRaisesRegex(ValueError, "checksum mismatch"):
                load_sample(folder)

    def test_daily_report_needs_no_private_workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch(
                "etf_backtest.api.load_market_data", side_effect=AssertionError("private data")
            ):
                run = run_demo(tmp)
            self.assertTrue(run.report_path.is_file())
            self.assertEqual(len(run.result.ledger), 2408)
            self.assertFalse(run.result.fills.empty)
            self.assertTrue((run.result.fills.signal_time < run.result.fills.timestamp).all())
            self.assertTrue((run.result.ledger.cash >= -1e-8).all())
            self.assertEqual(
                set(run.result.actions.kind), {"split", "dividend_accrual", "dividend_payment"}
            )
            self.assertIsNotNone(run.factor)


if __name__ == "__main__":
    unittest.main()
