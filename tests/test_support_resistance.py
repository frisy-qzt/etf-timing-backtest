import unittest

import pandas as pd

from etf_backtest.support_resistance import sr_target


def bars(close, high=None, low=None):
    s = pd.Series(close, index=pd.bdate_range("2024-01-01", periods=len(close)), dtype=float)
    return pd.DataFrame(
        {
            "signal_close": s,
            "signal_high": high if high is not None else s + 0.1,
            "signal_low": low if low is not None else s - 0.3,
            "valid": True,
            "frequency": "daily",
        }
    )


SMALL = dict(
    lookback=3,
    trend_period=2,
    slope_period=1,
    atr_period=2,
    buffer_atr=0.1,
    stop_atr=0.5,
    risk_atr=3.0,
    trailing_atr=10.0,
    max_hold=20,
    cooldown=2,
    retest_bars=3,
    range_slope=0.5,
    min_reward_risk=1.0,
)


class SupportResistanceTests(unittest.TestCase):
    def test_breakout_signals_at_close_and_stop_cooldown(self):
        d = bars([10, 10, 10, 11, 9, 12, 13, 14])
        out = sr_target(d, "breakout", **SMALL)
        self.assertEqual(out.tolist(), [0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0])

    def test_retest_uses_frozen_resistance_and_waits(self):
        d = bars([10, 10, 10, 11, 10.3, 11], low=[9.8, 9.8, 9.8, 10.7, 10.1, 10.8])
        out = sr_target(d, "retest", **SMALL)
        self.assertEqual(out.tolist(), [0.0, 0.0, 0.0, 0.0, 1.0, 1.0])

    def test_range_bounce_and_frozen_pressure_take_profit(self):
        d = bars([10, 10, 10, 9.3, 10.9], high=[11, 11, 11, 9.5, 11], low=[9, 9, 9, 8.95, 10.7])
        self.assertEqual(sr_target(d, "bounce", **SMALL).tolist(), [0.0, 0.0, 0.0, 1.0, 0.0])

    def test_spring_requires_previous_failed_breakdown(self):
        d = bars(
            [10, 10, 10, 8.7, 9.4, 10.9],
            high=[11, 11, 11, 9, 9.6, 11],
            low=[9, 9, 9, 8.6, 9.2, 10.7],
        )
        self.assertEqual(sr_target(d, "spring", **SMALL).tolist(), [0.0, 0.0, 0.0, 0.0, 1.0, 0.0])

    def test_all_modes_are_prefix_causal_and_preserve_invalid_rows(self):
        d = bars([10, 10, 10, 11, 10.3, 12, 11, 9, 10, 12, 14])
        d.loc[d.index[5], "valid"] = False
        for mode in ["breakout", "bounce", "retest", "spring"]:
            with self.subTest(mode=mode):
                full = sr_target(d, mode, **SMALL)
                self.assertTrue(pd.isna(full.iloc[5]))
                self.assertTrue(full.dropna().isin([0.0, 1.0]).all())
                for n in range(1, len(d)):
                    pd.testing.assert_series_equal(
                        full.iloc[:n], sr_target(d.iloc[:n], mode, **SMALL)
                    )
                clean = sr_target(d.loc[d.valid], mode, **SMALL)
                pd.testing.assert_series_equal(full.dropna(), clean, check_freq=False)

    def test_reject_bad_parameters_and_prices(self):
        d = bars([10] * 10)
        for kwargs in [
            dict(lookback=0),
            dict(lookback=2.5),
            dict(buffer_atr=-1),
            dict(trailing_atr=float("nan")),
            dict(cooldown=-1),
            dict(strategy="unknown"),
        ]:
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                sr_target(d, **kwargs)
        d.loc[d.index[1], "signal_close"] = float("inf")
        with self.assertRaises(ValueError):
            sr_target(d)

    def test_zero_atr_does_not_clear_then_resurrect_target_state(self):
        d = bars(
            [10, 10, 10, 11, 11, 11, 11, 11.2],
            high=[10.1, 10.1, 10.1, 11.1, 11, 11, 11, 11.3],
            low=[9.7, 9.7, 9.7, 10.7, 11, 11, 11, 11],
        )
        self.assertEqual(
            sr_target(d, "breakout", **SMALL).tolist(), [0.0, 0.0, 0.0, 1.0, 1.0, 1.0, 1.0, 1.0]
        )
        self.assertEqual(
            sr_target(d, "breakout", **{**SMALL, "max_hold": 3}).tolist(),
            [0.0, 0.0, 0.0, 1.0, 1.0, 1.0, 0.0, 0.0],
        )

    def test_retest_expiry_counts_zero_atr_bars(self):
        d = bars(
            [10, 10, 10, 11, 11, 11, 10.3, 10.35],
            high=[10.1, 10.1, 10.1, 11.1, 11, 11, 10.4, 10.4],
            low=[9.7, 9.7, 9.7, 10.7, 11, 11, 10.1, 10.1],
        )
        self.assertEqual(sr_target(d, "retest", **{**SMALL, "atr_period": 1}).tolist(), [0.0] * 8)


if __name__ == "__main__":
    unittest.main()
