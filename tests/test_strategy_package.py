import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd
import plotly.io as pio

from etf_backtest import BacktestConfig, run_backtest, run_strategy
from etf_backtest.engine import window_start
from etf_backtest.frequency import resample_minutes


def fixtures():
    days = pd.bdate_range("2026-01-05", periods=3)
    idx = pd.DatetimeIndex(
        [
            ts
            for day in days
            for ts in list(
                pd.date_range(
                    day + pd.Timedelta("9h30min"), day + pd.Timedelta("11h30min"), freq="min"
                )
            )
            + list(
                pd.date_range(day + pd.Timedelta("13h1min"), day + pd.Timedelta("15h"), freq="min")
            )
        ]
    )
    m = pd.DataFrame(
        dict(open=10.0, high=12.0, low=8.0, close=10.0, volume=10000.0, amount=100000.0), index=idx
    )
    daily = pd.DataFrame(dict(event_factor=1.0, valuation_close=10.0), index=days)
    return m, daily


class PackageTests(unittest.TestCase):
    def test_frequencies_and_conservation(self):
        m, d = fixtures()
        for n in [1, 2, 5, 7, 15, 30, 60, 90, 120]:
            with self.subTest(n=n):
                bars = resample_minutes(m, d, f"{n}min")
                self.assertEqual(len(bars), 3 * 2 * int(np.ceil(120 / n)))
                self.assertEqual(bars.amount.sum(), m.amount.sum())
                self.assertTrue(bars.valid.all())
                for t in bars.index:
                    self.assertLess(window_start(t, f"{n}min"), t)
                self.assertEqual(bars.index[-1].hour, 15)

    def test_missing_constituent_invalidates(self):
        m, d = fixtures()
        bars = resample_minutes(m.drop(m.index[3]), d, "5min")
        self.assertFalse(bars.valid.iloc[0])
        self.assertTrue(bars.missing_bar.iloc[0])

    def test_extreme_delay_t1_and_valuation(self):
        m, d = fixtures()
        bars = resample_minutes(m, d, "60min")
        targets = pd.Series([1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0], index=bars.index)
        c = BacktestConfig(initial_cash=1200, commission=0, slippage=0, intraday_price="extreme")
        r = run_backtest(bars, targets, c)
        self.assertEqual(r.fills.reference_price.tolist(), [12, 8])
        self.assertEqual(r.fills.quantity.tolist(), [100, 100])
        self.assertEqual(r.fills.timestamp.iloc[0], bars.index[1])
        self.assertEqual(r.fills.timestamp.iloc[1], bars.index[4])
        self.assertEqual(r.ledger.equity.iloc[1], 1000)
        self.assertEqual(r.ledger.equity.iloc[-1], 800)
        self.assertIn("t_plus_one", r.orders.reason.tolist())
        self.assertTrue((r.fills.signal_time < r.fills.timestamp).all())
        self.assertEqual(window_start(bars.index[2], "60min").hour, 13)

    def test_html_only_flat_path_and_chinese_report(self):
        previous_engine = pio.json.config.default_engine
        self.addCleanup(setattr, pio.json.config, "default_engine", previous_engine)
        for engine in ("auto", "json"):
            with self.subTest(engine=engine):
                pio.json.config.default_engine = engine
                m, d = fixtures()
                bars = resample_minutes(m, d, "5min")
                with tempfile.TemporaryDirectory() as tmp:
                    run = run_strategy(
                        "测试因子", "buy_hold", bars=bars, frequency="5min", output_root=tmp
                    )
                    self.assertEqual(run.report_path, Path(tmp).resolve() / "测试因子report.html")
                    self.assertEqual(run.output_dir, Path(tmp).resolve())
                    self.assertEqual([x.name for x in Path(tmp).iterdir()], ["测试因子report.html"])
                    text = run.report_path.read_text()
                    for label in [
                        "累计收益率",
                        "夏普比率",
                        "因子策略",
                        "买入持有",
                        "始终空仓",
                        "实际仓位",
                        "买入取最高价、卖出取最低价",
                        "数据诊断与复现信息",
                        "0.50 个基点",
                    ]:
                        self.assertTrue(
                            label in text or json.dumps(label, ensure_ascii=True)[1:-1] in text,
                            f"Missing Chinese report label: {label}",
                        )
                    for english in [
                        "NAV / benchmark",
                        "Daily drawdown",
                        "Closed trade returns",
                        "No closed trades",
                        "diagnostics.json",
                    ]:
                        self.assertNotIn(english, text)
                    changed = run_strategy(
                        "测试因子", "cash", bars=bars, frequency="5min", output_root=tmp
                    )
                    self.assertEqual(run.report_path, changed.report_path)
                    self.assertNotEqual(text, changed.report_path.read_text())
                    self.assertEqual(len(list(Path(tmp).iterdir())), 1)
                    with self.assertRaises(ValueError):
                        run_strategy(
                            "../escape", "cash", bars=bars, frequency="5min", output_root=tmp
                        )

    def test_failed_run_preserves_report_and_leaves_no_auxiliary_files(self):
        m, d = fixtures()
        bars = resample_minutes(m, d, "60min")
        with tempfile.TemporaryDirectory() as tmp:
            run = run_strategy("原子写入", "cash", bars=bars, frequency="60min", output_root=tmp)
            before = run.report_path.read_bytes()
            with patch("etf_backtest.report.os.replace", side_effect=OSError("模拟文件替换失败")):
                with self.assertRaises(OSError):
                    run_strategy(
                        "原子写入", "buy_hold", bars=bars, frequency="60min", output_root=tmp
                    )
            self.assertEqual(run.report_path.read_bytes(), before)
            self.assertEqual([x.name for x in Path(tmp).iterdir()], ["原子写入report.html"])
            with self.assertRaises(ValueError):
                run_strategy(
                    "原子写入",
                    pd.Series(2.0, index=bars.index),
                    bars=bars,
                    frequency="60min",
                    output_root=tmp,
                )
            self.assertEqual(run.report_path.read_bytes(), before)

    def test_effective_parameters_and_finite_limit_diagnostics(self):
        m, d = fixtures()
        bars = resample_minutes(m, d, "5min")
        bars["limit_up"] = np.inf
        bars["limit_down"] = np.inf
        with tempfile.TemporaryDirectory() as tmp:
            run = run_strategy(
                "参数诊断",
                "trend",
                bars=bars,
                frequency="5min",
                output_root=tmp,
                params={"fast": 10},
            )
            text = run.report_path.read_text()
            self.assertIn("<td>短均线根数</td><td>10</td>", text)
            self.assertIn("<td>长均线根数</td><td>60</td>", text)
            self.assertIn("<td>评估区间涨跌停价覆盖率</td><td>0.00%</td>", text)

    def test_limits_and_suspension_propagate(self):
        m, d = fixtures()
        d["limit_up"] = 8.0
        d["suspended"] = True
        bars = resample_minutes(m, d, "5min")
        self.assertTrue(bars.suspended.all())
        targets = pd.Series(1.0, index=bars.index)
        self.assertTrue(run_backtest(bars, targets).fills.empty)
        bars["suspended"] = False
        self.assertTrue(run_backtest(bars, targets).fills.empty)

    def test_series_alignment_and_model_precedence(self):
        m, d = fixtures()
        bars = resample_minutes(m, d, "60min")
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "exactly align"):
                run_strategy(
                    "wrong", pd.Series(dtype=float), bars=bars, frequency="60min", output_root=tmp
                )
            r = run_strategy(
                "explicit",
                "buy_hold",
                bars=bars,
                frequency="60min",
                output_root=tmp,
                config=BacktestConfig(intraday_price="vwap"),
            )
            self.assertEqual(r.result.fills.reference_price.iloc[0], 10.0)
            r = run_strategy(
                "override",
                "buy_hold",
                bars=bars,
                frequency="60min",
                output_root=tmp,
                config=BacktestConfig(intraday_price="vwap"),
                intraday_price="extreme",
            )
            self.assertEqual(r.result.fills.reference_price.iloc[0], 12.0)

    def test_callable_and_interval_isolation(self):
        m, d = fixtures()
        bars = resample_minutes(m, d, "1min")

        def custom(frame, window):
            return frame.signal_close.rolling(window).mean().notna().astype(float)

        with tempfile.TemporaryDirectory() as tmp:
            r = run_strategy(
                "callable",
                custom,
                bars=bars,
                frequency="1min",
                params={"window": 5},
                output_root=tmp,
                config=BacktestConfig(start="2026-01-06", end="2026-01-06"),
            )
            self.assertEqual(r.result.ledger.cash.iloc[0], 1_000_000)
            self.assertEqual(r.result.ledger.shares.iloc[0], 0)
            self.assertGreater(r.result.fills.signal_time.min(), pd.Timestamp("2026-01-06"))


if __name__ == "__main__":
    unittest.main()
