"""The synthetic example must generate valid data and a complete report."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from examples.synthetic_demo import make_bars, run_demo


class SyntheticDemoTests(unittest.TestCase):
    def test_fixture_is_repeatable_and_valid(self):
        bars = make_bars()
        pd.testing.assert_frame_equal(bars, make_bars())
        self.assertTrue(bars.index.is_monotonic_increasing)
        self.assertFalse(bars.index.has_duplicates)
        self.assertTrue((bars.low <= bars[["open", "close"]].min(axis=1)).all())
        self.assertTrue((bars.high >= bars[["open", "close"]].max(axis=1)).all())
        self.assertTrue((bars.volume > 0).all())

    def test_report_uses_synthetic_data_and_retains_delayed_execution(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch(
                "etf_backtest.api.load_market_data",
                side_effect=AssertionError("Unexpected external data loading"),
            ):
                run = run_demo(folder)
            self.assertEqual(list(Path(folder).resolve().iterdir()), [run.report_path])
            self.assertIn("合成数据均线", run.report_path.read_text(encoding="utf-8"))
            self.assertIsNotNone(run.factor)
            self.assertFalse(run.result.fills.empty)
            self.assertTrue((run.result.fills.signal_time < run.result.fills.timestamp).all())
            ledger = run.result.ledger
            self.assertTrue((ledger.cash >= -1e-8).all())
            self.assertTrue((ledger.shares >= 0).all())


if __name__ == "__main__":
    unittest.main()
