import unittest

import pandas as pd

from etf_backtest.data import (
    aggregate_hourly,
    complete_hourly_grid,
    event_adjusted_prices,
    normalize_units,
)
from etf_backtest.research import split_dates


class DataTests(unittest.TestCase):
    def test_minute_rebuild_includes_opening_auction_only_once(self):
        idx = pd.date_range("2023-06-05 09:30", "2023-06-05 11:30", freq="min").append(
            pd.date_range("2023-06-05 13:01", "2023-06-05 15:00", freq="min")
        )
        minute = pd.DataFrame(
            {"open": 10.0, "close": 10.0, "high": 10.0, "low": 10.0, "volume": 1, "amount": 1000},
            index=idx,
        )
        result = aggregate_hourly(minute)
        self.assertEqual(result.volume.tolist(), [61, 60, 60, 60])
        self.assertEqual(result.amount.sum(), 241000)
        with self.assertRaises(ValueError):
            aggregate_hourly(minute.iloc[1:])

    def test_daily_thousand_yuan_and_hands(self):
        d = pd.DataFrame({"amount": [200.0], "volume": [1000.0]})
        n = normalize_units(d, "daily")
        self.assertEqual(n.amount.iloc[0], 200000.0)
        self.assertEqual(n.volume.iloc[0], 100000.0)
        self.assertEqual(n.vwap.iloc[0], 2.0)
        self.assertEqual(d.amount.iloc[0], 200.0)

    def test_split_adjustment_uses_no_future_events(self):
        d = pd.DataFrame(
            {
                "close": [10.0, 10.0, 20.0, 20.0],
                "open": [10.0, 10.0, 20.0, 20.0],
                "high": [10.0, 10.0, 20.0, 20.0],
                "low": [10.0, 10.0, 20.0, 20.0],
                "volume": [1, 0, 1, 1],
            },
            index=pd.to_datetime(["2022-09-01", "2022-09-02", "2022-09-05", "2022-09-06"]),
        )
        ev = pd.DataFrame(
            [{"kind": "split", "ex_date": "2022-09-02", "ratio": 0.5, "verified": True}]
        )
        out = event_adjusted_prices(d, ev)
        self.assertEqual(out.signal_close.tolist(), [10.0, 10.0, 10.0, 10.0])
        ev2 = pd.concat(
            [
                ev,
                pd.DataFrame(
                    [{"kind": "split", "ex_date": "2023-01-01", "ratio": 2.0, "verified": True}]
                ),
            ]
        )
        pd.testing.assert_series_equal(out.signal_close, event_adjusted_prices(d, ev2).signal_close)

    def test_missing_day_is_explicit_four_untradable_slots(self):
        idx = pd.to_datetime(
            ["2022-01-04 10:30", "2022-01-04 11:30", "2022-01-04 14:00", "2022-01-04 15:00"]
        )
        h = pd.DataFrame(
            {
                "close": 10.0,
                "open": 10.0,
                "high": 10.0,
                "low": 10.0,
                "volume": 100.0,
                "amount": 1000.0,
                "valid": True,
            },
            index=idx,
        )
        daily = pd.DataFrame(
            {"close": [10.0, 11.0]}, index=pd.to_datetime(["2022-01-04", "2022-01-05"])
        )
        out = complete_hourly_grid(h, daily)
        self.assertEqual(len(out), 8)
        self.assertFalse(out.iloc[4:].valid.any())
        self.assertEqual(out.iloc[-1].mark_close, 11.0)

    def test_missing_split_day_close_uses_event_adjusted_valuation(self):
        h = pd.DataFrame(
            {"close": [10.0], "valid": [True]}, index=pd.to_datetime(["2022-09-01 15:00"])
        )
        daily = pd.DataFrame(
            {"close": [10.0, 10.0], "valuation_close": [10.0, 20.0]},
            index=pd.to_datetime(["2022-09-01", "2022-09-02"]),
        )
        out = complete_hourly_grid(h, daily)
        self.assertEqual(out.loc["2022-09-02 15:00", "mark_close"], 20.0)

    def test_split_by_days_keeps_hourly_day_intact(self):
        idx = pd.date_range("2022-01-01", periods=10, freq="D")
        out = split_dates(idx)
        self.assertEqual(out["train"], ("2022-01-01", "2022-01-06"))
        self.assertEqual(out["validation"], ("2022-01-07", "2022-01-08"))
        self.assertEqual(out["test"], ("2022-01-09", "2022-01-10"))


if __name__ == "__main__":
    unittest.main()
