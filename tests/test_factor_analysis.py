"""因子检验：IC／ICIR／衰减／分组多空，全部使用未二值化因子值。"""

import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from etf_backtest import run_strategy
from etf_backtest.factor import (
    bars_per_year,
    default_ic_window,
    factor_report,
    forward_returns,
    ic_series,
    ic_table,
    quantile_analysis,
)
from etf_backtest.frequency import resample_minutes
from etf_backtest.strategies import raw_factor


def random_walk(periods=900, seed=7):
    """独立收益的随机漫步价格，索引为工作日。"""
    rng = np.random.default_rng(seed)
    returns = pd.Series(rng.normal(0, 0.01, periods))
    index = pd.bdate_range("2024-01-01", periods=periods)
    return pd.DataFrame({"signal_close": 100 * (1 + returns).cumprod().to_numpy()}, index=index)


def bars_from_minutes(days=6):
    """合成分钟行情并重采样为5分钟K线，供报告集成测试使用。"""
    index = pd.DatetimeIndex(
        [
            ts
            for day in pd.bdate_range("2026-01-05", periods=days)
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
    steps = np.linspace(10, 12, len(index))
    minute = pd.DataFrame(
        dict(
            open=steps,
            high=steps + 0.05,
            low=steps - 0.05,
            close=steps,
            volume=10000.0,
            amount=steps * 10000.0,
        ),
        index=index,
    )
    daily = pd.DataFrame(
        dict(event_factor=1.0, valuation_close=steps[-1]), index=index.normalize().unique()
    )
    return resample_minutes(minute, daily, "5min")


class FactorMathTests(unittest.TestCase):
    def test_perfect_foresight_factor_has_unit_ic(self):
        bars = random_walk()
        ret = bars.signal_close.pct_change().shift(-1)
        table = ic_table(ret, bars, "daily", horizons=[1], window=30)
        for method in ("pearson", "spearman"):
            row = table[(table.horizon == 1) & (table.method == method)].iloc[0]
            self.assertAlmostEqual(row.ic_mean, 1.0, places=6)
            self.assertAlmostEqual(row.full_sample_ic, 1.0, places=6)
            self.assertAlmostEqual(row.positive_ratio, 1.0)
        # 899 个对齐样本按 30 个一窗，得到 29 个独立窗口
        self.assertEqual(int(table.blocks.iloc[0]), 29)
        self.assertEqual(int(table.observations.iloc[0]), len(bars) - 1)

    def test_reversed_factor_is_negative_and_ic_decays(self):
        bars = random_walk(periods=2000)
        rng = np.random.default_rng(11)
        noise = pd.Series(rng.normal(0, 0.01, len(bars)), index=bars.index)
        forward = bars.signal_close.pct_change().shift(-1)
        table = ic_table(-(forward + noise), bars, "daily", horizons=[1, 5, 20], window=20)
        single = table[(table.horizon == 1) & (table.method == "pearson")].iloc[0]
        self.assertLess(single.ic_mean, -0.5)
        self.assertLess(single.icir, 0)
        self.assertLess(single.t_stat, 0)
        self.assertLess(single.positive_ratio, 0.1)
        # 反向因子的 IC 为负，衰减体现为绝对值向零收敛
        decay = table[table.method == "pearson"].set_index("horizon").ic_mean.abs()
        self.assertGreater(decay.loc[1], decay.loc[5])
        self.assertGreater(decay.loc[5], decay.loc[20])

    def test_horizon_sampling_is_non_overlapping(self):
        bars = random_walk()
        series = ic_series(bars.signal_close.pct_change().shift(-5), bars, 5, window=10)
        frame = forward_returns(bars, [5])[5].dropna()
        self.assertEqual(len(frame), len(bars) - 5)
        self.assertEqual(list(series.index), list(frame.index[::5][9::10]))
        self.assertEqual(len(series), len(frame[::5]) // 10)

    def test_constant_and_short_factor_are_safe(self):
        bars = random_walk(periods=80)
        table = ic_table(pd.Series(1.0, index=bars.index), bars, "daily", horizons=[1], window=20)
        self.assertTrue(np.isnan(table.ic_mean.iloc[0]))
        self.assertTrue(np.isnan(table.icir.iloc[0]))
        groups, nav, monotonicity = quantile_analysis(
            pd.Series(1.0, index=bars.index), bars, "daily", quantiles=3, window=20
        )
        self.assertTrue(groups.empty or np.isnan(monotonicity))
        self.assertEqual(bars_per_year("daily"), 252.0)
        self.assertAlmostEqual(bars_per_year("5min"), 252.0 * 240.0 / 5)

    def test_group_monotonicity_and_long_short_spread(self):
        bars = random_walk(periods=1200)
        factor = bars.signal_close.pct_change().shift(-1)
        groups, nav, monotonicity = quantile_analysis(factor, bars, "daily", quantiles=5, window=60)
        self.assertFalse(groups.empty)
        self.assertAlmostEqual(monotonicity, 1.0, places=6)
        ordered = groups[groups.group != "long_short"]
        self.assertEqual(ordered.conditional_return.is_monotonic_increasing, True)
        spread = groups[groups.group == "long_short"].iloc[0]
        self.assertGreater(spread.annual_return, 0)
        self.assertGreater(spread.final_nav, 1.0)
        self.assertIn("多空组合", nav.columns)
        self.assertEqual(
            list(nav.columns), ["分组1", "分组2", "分组3", "分组4", "分组5", "多空组合"]
        )
        # 多空净值等于最高组与最低组择时净值的复合差
        self.assertTrue(np.isfinite(spread.max_drawdown))
        self.assertGreaterEqual(spread.win_rate, 0.0)

    def test_quantile_labels_use_only_past_samples(self):
        bars = random_walk(periods=400)
        factor = bars.signal_close.pct_change().shift(-1)
        short, short_nav, _ = quantile_analysis(
            factor, bars.iloc[:300], "daily", quantiles=4, window=40
        )
        long, long_nav, _ = quantile_analysis(factor, bars, "daily", quantiles=4, window=40)
        self.assertFalse(short_nav.empty)
        # 追加未来数据不改变历史分组：共同日期上的分组净值完全一致
        pd.testing.assert_frame_equal(short_nav, long_nav.loc[short_nav.index])
        self.assertTrue(np.isfinite(short.conditional_return).all())


class RawFactorTests(unittest.TestCase):
    def test_raw_factors_match_signal_warmup(self):
        bars = bars_from_minutes()
        trend = raw_factor(bars, "trend", {"fast": 10, "slow": 30})
        self.assertEqual(len(trend), len(bars))
        close = bars.loc[bars.valid].signal_close
        self.assertTrue(trend.isna().sum() >= int((~close.rolling(30).mean().notna()).sum()))
        self.assertIsNone(raw_factor(bars, "buy_hold"))
        self.assertIsNone(raw_factor(bars, "cash"))
        with self.assertRaises(ValueError):
            raw_factor(bars, "unknown")
        momentum = raw_factor(bars, "momentum", {"lookback": 12})
        expected = close / close.shift(12) - 1
        pd.testing.assert_series_equal(
            momentum.dropna().reindex(expected.dropna().index), expected.dropna(), check_names=False
        )
        # 均值回归因子是标准分：价格高于滚动均值时为正
        average, deviation = close.rolling(20).mean(), close.rolling(20).std()
        expected_z = ((close - average) / deviation.replace(0, np.nan)).dropna()
        pd.testing.assert_series_equal(
            raw_factor(bars, "mean_reversion").dropna(), expected_z, check_names=False
        )
        self.assertGreater(raw_factor(bars, "mean_reversion").dropna().iloc[-1], 0)


class FactorReportIntegrationTests(unittest.TestCase):
    def test_builtin_strategy_adds_chinese_factor_sections(self):
        bars = bars_from_minutes()
        with tempfile.TemporaryDirectory() as tmp:
            run = run_strategy(
                "趋势因子",
                "trend",
                bars=bars,
                frequency="5min",
                output_root=tmp,
                params={"fast": 10, "slow": 30},
                horizons=[1, 3, 6],
                quantiles=3,
                ic_window=20,
                group_window=40,
            )
            self.assertEqual([x.name for x in Path(tmp).iterdir()], ["趋势因子report.html"])
            self.assertIsNotNone(run.factor)
            self.assertIn("内置策略原始因子", run.factor.name)
            self.assertGreater(len(run.factor.ic), 0)
            text = run.report_path.read_text()
            for label in [
                "因子 IC 与 ICIR",
                "IC 衰减曲线",
                "因子分组收益对照",
                "分组与多空组合净值",
                "未二值化",
                "多空组合（最高组做多、最低组做空）",
                "Spearman 秩相关",
                "IC 按每 20 个非重叠样本为一个独立窗口计算",
                "Rank IC 均值",
                "独立窗口数",
                "全样本IC",
            ]:
                self.assertIn(label, text)
            self.assertNotIn("Rolling IC", text)
            self.assertIn("因子值指纹", text)

    def test_custom_factor_series_is_used_and_never_binarized(self):
        bars = bars_from_minutes()
        factor = bars.signal_close.pct_change(6)
        with tempfile.TemporaryDirectory() as tmp:
            run = run_strategy(
                "自定义因子",
                "cash",
                bars=bars,
                frequency="5min",
                output_root=tmp,
                factor=factor,
                horizons=[1, 3],
                quantiles=3,
                ic_window=15,
                group_window=30,
            )
            self.assertIsNotNone(run.factor)
            self.assertIn("外部因子序列", run.factor.name)
            self.assertEqual([x.name for x in Path(tmp).iterdir()], ["自定义因子report.html"])
        with tempfile.TemporaryDirectory() as tmp:
            # 原值等比例放大不改变相关系数与分组，说明检验用的是原始值而非0/1化结果
            scaled = run_strategy(
                "放大因子",
                "cash",
                bars=bars,
                frequency="5min",
                output_root=tmp,
                factor=factor * 100,
                horizons=[1, 3],
                quantiles=3,
                ic_window=15,
                group_window=30,
            )
            pd.testing.assert_frame_equal(run.factor.ic, scaled.factor.ic)
            pd.testing.assert_frame_equal(run.factor.groups, scaled.factor.groups)
        with tempfile.TemporaryDirectory() as tmp:
            # 显式传入已二值化的因子时结果不同，说明系统未偷偷替换成0/1版本
            binary = run_strategy(
                "二值因子",
                "cash",
                bars=bars,
                frequency="5min",
                output_root=tmp,
                factor=(factor > 0).astype(float),
                horizons=[1, 3],
                quantiles=3,
                ic_window=15,
                group_window=30,
            )
            pd.testing.assert_index_equal(run.factor.ic.index, binary.factor.ic.index)
            self.assertFalse(np.allclose(run.factor.ic.ic_mean, binary.factor.ic.ic_mean))
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                run_strategy(
                    "错位因子",
                    "cash",
                    bars=bars,
                    frequency="5min",
                    output_root=tmp,
                    factor=factor.iloc[:-1],
                )
        with tempfile.TemporaryDirectory() as tmp:
            plain = run_strategy("无因子", "cash", bars=bars, frequency="5min", output_root=tmp)
            self.assertIsNone(plain.factor)
            self.assertNotIn("因子 IC 与 ICIR", plain.report_path.read_text())

    def test_callable_factor_and_disabled_analysis(self):
        bars = bars_from_minutes()

        def factor(bars, window=8):
            return bars.signal_close.pct_change(window)

        with tempfile.TemporaryDirectory() as tmp:
            run = run_strategy(
                "函数因子",
                "cash",
                bars=bars,
                frequency="5min",
                output_root=tmp,
                factor=factor,
                params={"window": 8},
                horizons=[1, 3],
                quantiles=3,
                ic_window=15,
                group_window=30,
            )
            self.assertIsNotNone(run.factor)
            self.assertIn("自定义因子函数", run.factor.name)
            summary = run.factor.summary()
            self.assertIn("spearman_icir", summary)
            with tempfile.TemporaryDirectory() as other:
                off = run_strategy(
                    "关闭检验",
                    "trend",
                    bars=bars,
                    frequency="5min",
                    output_root=other,
                    factor_analysis=False,
                    params={"fast": 10, "slow": 30},
                )
                self.assertIsNone(off.factor)
                self.assertNotIn("因子 IC 与 ICIR", off.report_path.read_text())

    def test_factor_report_prints_all_horizons(self):
        bars = random_walk(periods=600)
        factor = bars.signal_close.pct_change().shift(-1)
        rep = factor_report(
            factor,
            bars,
            "daily",
            name="测试因子",
            horizons=[1, 5, 20],
            quantiles=4,
            ic_window=30,
            group_window=60,
        )
        self.assertEqual(list(rep.decay.index), [1, 5, 20])
        self.assertEqual(list(rep.decay.columns), ["pearson", "spearman"])
        self.assertTrue(rep.available)
        self.assertAlmostEqual(rep.summary()["spearman_ic"], 1.0, places=6)
        # 未显式指定窗口时按频率自动取值：日频60，分钟频240
        self.assertEqual(default_ic_window("daily"), 60)
        self.assertEqual(default_ic_window("5min"), 240)
        self.assertEqual(factor_report(factor, bars, "daily").ic_window, 60)
        self.assertEqual(factor_report(factor, bars, "5min").ic_window, 240)


if __name__ == "__main__":
    unittest.main()
