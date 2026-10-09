"""Hand-calculated accounting cases: each assertion protects economic behavior."""

import unittest

import numpy as np
import pandas as pd

from etf_backtest.engine import BacktestConfig, run_backtest
from etf_backtest.metrics import performance
from etf_backtest.strategies import signal


def bars(times, prices, frequency="daily", volume=100000):
    d = pd.DataFrame(
        {
            "datetime": pd.to_datetime(times),
            "open": prices,
            "high": np.array(prices) + 1,
            "low": np.array(prices) - 1,
            "close": prices,
            "volume": volume,
        }
    )
    d["amount"] = d.close * d.volume
    d["signal_close"] = d.close
    d["signal_high"] = d.high
    d["signal_low"] = d.low
    d["valid"] = True
    d["frequency"] = frequency
    return d.set_index("datetime")


def cfg(**kwargs):
    return BacktestConfig(initial_cash=10000, commission=0, slippage=0, **kwargs)


class EngineTests(unittest.TestCase):
    def test_daily_open_fill_does_not_depend_on_future_high_or_volume(self):
        d = bars(["2022-01-04", "2022-01-05", "2022-01-06"], [10, 10, 10])
        t = pd.Series([1, 1, 1], index=d.index)
        before = run_backtest(d, t, cfg())
        d.loc[d.index[1], ["high", "close", "amount", "volume"]] = np.nan
        after = run_backtest(d, t, cfg())
        self.assertEqual(after.fills.iloc[0].timestamp, before.fills.iloc[0].timestamp)
        self.assertEqual(after.fills.iloc[0].quantity, 1000)

    def test_optional_capacity_filter_rejects_impossible_order(self):
        d = bars(["2022-01-04", "2022-01-05"], [10, 10], volume=100)
        r = run_backtest(d, pd.Series([1, 1], index=d.index), cfg(max_participation=0.1))
        self.assertTrue(r.fills.empty)
        self.assertTrue((r.orders.reason == "capacity_limit").any())

    def test_daily_signal_fills_next_open_not_current_close(self):
        d = bars(["2022-01-04", "2022-01-05", "2022-01-06"], [10, 20, 30])
        r = run_backtest(d, pd.Series([1, 1, 0], index=d.index), cfg())
        self.assertEqual(len(r.fills), 1)
        self.assertEqual(r.fills.iloc[0].quantity, 500)
        self.assertEqual(r.fills.iloc[0].reference_price, 20)
        self.assertEqual(r.ledger.iloc[-1].equity, 15000)
        self.assertEqual(r.fills.iloc[0].timestamp, pd.Timestamp("2022-01-05 09:30"))

    def test_hourly_vwap_and_t1_cross_lunch_and_overnight(self):
        d = bars(
            [
                "2022-01-04 10:30",
                "2022-01-04 11:30",
                "2022-01-04 14:00",
                "2022-01-04 15:00",
                "2022-01-05 10:30",
            ],
            [10, 10, 10, 10, 12],
            "60min",
        )
        r = run_backtest(d, pd.Series([0, 1, 0, 0, 0], index=d.index), cfg())
        self.assertEqual(r.fills.side.tolist(), ["buy", "sell"])
        self.assertEqual(r.fills.iloc[0].window_start, pd.Timestamp("2022-01-04 13:00"))
        self.assertEqual(r.fills.iloc[0].timestamp, pd.Timestamp("2022-01-04 14:00"))
        self.assertEqual(r.fills.iloc[1].timestamp, pd.Timestamp("2022-01-05 10:30"))
        self.assertEqual(r.ledger.iloc[-1].equity, 12000)
        self.assertTrue((r.orders.reason == "t_plus_one").any())

    def test_vwap_not_close_and_no_double_counted_return(self):
        d = bars(["2022-01-04 10:30", "2022-01-04 11:30"], [10, 12], "60min")
        d.loc[d.index[1], "amount"] = 1000000
        d.loc[d.index[1], "low"] = 9
        r = run_backtest(d, pd.Series([1, 1], index=d.index), cfg())
        self.assertEqual(r.fills.iloc[0].reference_price, 10)
        self.assertEqual(r.ledger.iloc[-1].equity, 12000)

    def test_zero_minimum_fee_cash_and_lot_rounding(self):
        d = bars(["2022-01-04", "2022-01-05"], [10, 10])
        c = BacktestConfig(initial_cash=10000, commission=0.00005, slippage=0.0001)
        r = run_backtest(d, pd.Series([1, 1], index=d.index), c)
        self.assertEqual(r.fills.iloc[0].quantity, 900)
        self.assertAlmostEqual(r.fills.iloc[0].commission, 0.450045)
        self.assertAlmostEqual(r.ledger.iloc[-1].cash, 998.649955)

    def test_missing_window_cannot_fill_and_new_target_cancels(self):
        d = bars(
            ["2022-01-04 10:30", "2022-01-04 11:30", "2022-01-04 14:00"], [10, 10, 10], "60min"
        )
        d.loc[d.index[1], "valid"] = False
        r = run_backtest(d, pd.Series([1, 0, 0], index=d.index), cfg())
        # Missing bars cannot generate fresh signals, so the last valid target persists.
        self.assertEqual(r.fills.iloc[0].timestamp, pd.Timestamp("2022-01-04 14:00"))

    def test_range_resets_cash_and_discards_pre_range_signal(self):
        d = bars(["2021-12-31", "2022-01-04", "2022-01-05"], [10, 20, 20])
        r = run_backtest(d, pd.Series([1, 0, 0], index=d.index), cfg(start="2022-01-01"))
        self.assertTrue(r.fills.empty)
        self.assertEqual(r.ledger.iloc[0].equity, 10000)

    def test_split_on_suspended_day_preserves_wealth(self):
        d = bars(["2022-09-01", "2022-09-02", "2022-09-05", "2022-09-06"], [10, 10, 20, 20])
        # Buy first on 9/2; split the next day while no trading takes place.
        d.loc[d.index[2], ["volume", "amount"]] = 0
        d.loc[d.index[2], "close"] = 10  # stale raw quote must not halve wealth
        ev = pd.DataFrame(
            [{"kind": "split", "ex_date": "2022-09-05", "ratio": 0.5, "verified": True}]
        )
        r = run_backtest(d, pd.Series([1, 1, 1, 1], index=d.index), cfg(), ev)
        self.assertEqual(r.ledger.iloc[2].shares, 500)
        self.assertEqual(r.ledger.iloc[2].equity, 10000)
        self.assertEqual(r.ledger.iloc[-1].equity, 10000)

    def test_dividend_receivable_survives_sale_until_payment(self):
        d = bars(["2022-01-03", "2022-01-04", "2022-01-05", "2022-01-06"], [10, 10, 9, 9])
        ev = pd.DataFrame(
            [
                {
                    "kind": "dividend",
                    "record_date": "2022-01-04",
                    "ex_date": "2022-01-05",
                    "pay_date": "2022-01-06",
                    "cash_per_share": 1.0,
                    "verified": True,
                }
            ]
        )
        r = run_backtest(d, pd.Series([1, 0, 0, 0], index=d.index), cfg(), ev)
        self.assertEqual(r.ledger.iloc[2].cash, 9000)
        self.assertEqual(r.ledger.iloc[2].receivable, 1000)
        self.assertEqual(r.ledger.iloc[-1].cash, 10000)
        self.assertEqual(r.trades.iloc[0].pnl, 0)

    def test_unverified_event_blocks_backtest(self):
        d = bars(["2022-01-04", "2022-01-05"], [10, 10])
        ev = pd.DataFrame(
            [{"kind": "split", "ex_date": "2022-01-05", "ratio": 0.5, "verified": False}]
        )
        with self.assertRaises(ValueError):
            run_backtest(d, pd.Series([1, 1], index=d.index), cfg(), ev)

    def test_one_price_limit_blocks_buy(self):
        d = bars(["2022-01-04", "2022-01-05"], [10, 11])
        d.loc[d.index[1], ["low", "high", "limit_up"]] = 11
        r = run_backtest(d, pd.Series([1, 1], index=d.index), cfg())
        self.assertTrue(r.fills.empty)


class ResearchTests(unittest.TestCase):
    def test_mid_history_suspension_does_not_reset_trend_warmup(self):
        d = bars(pd.bdate_range("2020-01-01", periods=100), np.arange(100) + 10.0)
        d.loc[d.index[70], "valid"] = False
        s = signal(d, "trend", {"fast": 2, "slow": 5})
        self.assertTrue(pd.isna(s.iloc[70]))
        self.assertTrue((s.iloc[71:] == 1).all())

    def test_signals_are_prefix_invariant(self):
        d = bars(pd.bdate_range("2020-01-01", periods=130), 10 + np.sin(np.arange(130) / 5))
        altered = d.copy()
        altered.loc[altered.index[100] :, "signal_close"] = 1000
        for name in ["trend", "breakout", "momentum", "mean_reversion"]:
            pd.testing.assert_series_equal(
                signal(d, name).iloc[:100], signal(altered, name).iloc[:100]
            )

    def test_first_day_loss_is_included_in_metrics(self):
        d = bars(["2022-01-04", "2022-01-05"], [10, 10])
        r = run_backtest(d, pd.Series([1, 1], index=d.index), cfg())
        m = performance(r)
        self.assertEqual(m["total_return"], 0)
        self.assertEqual(m["max_drawdown"], 0)


if __name__ == "__main__":
    unittest.main()
