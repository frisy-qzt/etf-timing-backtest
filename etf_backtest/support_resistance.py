"""中证1000ETF支撑／压力择时，仅 pandas + 标准库。

接口：strategy(bars, **params) -> 与 bars.index 对齐的 0/1/NaN Series。
使用框架的 signal_high/low/close（同一复权口径）；不对目标仓位 shift。
默认参数以日线的有效K线计数；分钟线需重新设定窗口与验证交易成本。
止损／止盈均为收盘确认，下一可成交K线执行，并非盘中触价成交。
状态是目标仓位，无法感知引擎的拒单、T+1、实际成交价或实际持仓。
参数只是研究起点，未做盈利筛选；建议按时间划分训练／验证／样本外。

四种模式：
breakout: 上升趋势中突破过去N根最高价，旧压力转支撑，ATR跟踪退出。
bounce: 均线斜率较平缓时，测试箱底后收回，目标为入场时的箱顶。
retest: 突破后等待回踩固定旧压力位，超时／跌破后撤销等待。
spring: 前一根跌破旧支撑，本根重新收复，目标为跌破前的箱顶。

用法（项目根目录或安装当前源码包后）：
    from etf_backtest import BacktestConfig, run_strategy
    from etf_backtest.support_resistance import sr_breakout
    run = run_strategy('中证1000压力突破', sr_breakout, frequency='daily',
                       data_root='.', params={'lookback': 60},
                       config=BacktestConfig(start='2022-01-01', end='2026-09-30'))
其他函数：sr_bounce、sr_retest、sr_spring；也可直接调用 sr_target，
params={'strategy': 'retest', 'lookback': 60}。不将二值仓位当连续因子。
"""

from math import isfinite
from numbers import Integral, Real

import pandas as pd


def sr_target(
    bars: pd.DataFrame,
    strategy: str = "breakout",
    *,
    lookback: int = 60,
    trend_period: int = 120,
    slope_period: int = 10,
    atr_period: int = 14,
    buffer_atr: float = 0.2,
    stop_atr: float = 0.8,
    risk_atr: float = 2.5,
    trailing_atr: float = 3.0,
    max_hold: int = 60,
    cooldown: int = 3,
    retest_bars: int = 10,
    range_slope: float = 0.03,
    min_reward_risk: float = 1.5,
) -> pd.Series:
    """生成做多／空仓目标。暖机为0，无效行NaN，不修改输入。

    lookback/trend_period/slope_period/atr_period/max_hold/cooldown/retest_bars
    均以有效K线计。buffer_atr为突破、回踩和止盈缓冲；stop_atr为结构止损
    缓冲；risk_atr为入场初始最大风险；trailing_atr为最高收盘价回撤阈值。
    range_slope为 slope_period 根间均线变化绝对值上限，用于bounce/spring。
    min_reward_risk使用扣除buffer_atr后的箱顶目标和初始止损评估。
    每次入场冻结结构止损和箱顶目标；跟踪止损只上调，ATR变化不放宽。
    """
    if strategy not in ("breakout", "bounce", "retest", "spring"):
        raise ValueError("strategy must be breakout, bounce, retest or spring")
    periods = dict(
        lookback=lookback,
        trend_period=trend_period,
        slope_period=slope_period,
        atr_period=atr_period,
        max_hold=max_hold,
        retest_bars=retest_bars,
    )
    for key, value in {**periods, "cooldown": cooldown}.items():
        if (
            isinstance(value, bool)
            or not isinstance(value, Integral)
            or value < (0 if key == "cooldown" else 1)
        ):
            raise ValueError(f"{key} must be an integer >= {0 if key == 'cooldown' else 1}")
    for key, value in dict(
        buffer_atr=buffer_atr,
        stop_atr=stop_atr,
        risk_atr=risk_atr,
        trailing_atr=trailing_atr,
        range_slope=range_slope,
        min_reward_risk=min_reward_risk,
    ).items():
        if (
            isinstance(value, bool)
            or not isinstance(value, Real)
            or not isfinite(value)
            or value < 0
            or (key not in ("buffer_atr", "range_slope") and value == 0)
        ):
            raise ValueError(
                f"{key} must be finite and positive (buffer_atr/range_slope may be zero)"
            )
    required = ["valid", "signal_high", "signal_low", "signal_close"]
    missing = [key for key in required if key not in bars]
    if missing:
        raise ValueError(f"Missing columns: {missing}")
    if (
        not isinstance(bars.index, pd.DatetimeIndex)
        or not bars.index.is_monotonic_increasing
        or bars.index.has_duplicates
    ):
        raise ValueError("bars must have a sorted unique DatetimeIndex")
    if bars.valid.isna().any() or not bars.valid.isin([True, False]).all():
        raise ValueError("valid must contain nonmissing booleans")
    d = bars.loc[bars.valid.astype(bool), required[1:]].astype(float).copy()
    if (
        not d.map(isfinite).all().all()
        or not d.gt(0).all().all()
        or (d.signal_high < d.signal_close).any()
        or (d.signal_low > d.signal_close).any()
    ):
        raise ValueError("Valid signal OHLC must be finite, positive and low <= close <= high")
    close, high, low = d.signal_close, d.signal_high, d.signal_low
    # 参考价位、ATR和趋势基准只来自当前K线以前；不用居中窗口／未来枢轴。
    d["resistance"] = high.rolling(lookback).max().shift(1)
    d["support"] = low.rolling(lookback).min().shift(1)
    previous = close.shift(1)
    tr = pd.concat([high - low, (high - previous).abs(), (low - previous).abs()], axis=1).max(
        axis=1
    )
    d["atr"] = tr.rolling(atr_period).mean().shift(1)
    d["ma"] = close.rolling(trend_period).mean().shift(1)
    d["slope"] = d.ma / d.ma.shift(slope_period) - 1
    d["previous_close"] = previous
    d["previous_support"] = d.support.shift(1)
    d["previous_resistance"] = d.resistance.shift(1)
    d["previous_atr"] = d.atr.shift(1)

    targets = pd.Series(0.0, index=d.index, name="target")
    holding, stop, peak, take_profit, age, wait = False, 0.0, 0.0, None, 0, 0
    armed_level, armed_age = None, 0
    for i, row in enumerate(d.itertuples()):
        c, h, bar_low, a = row.signal_close, row.signal_high, row.signal_low, row.atr
        ready = (
            all(isfinite(x) for x in (a, row.ma, row.slope, row.support, row.resistance)) and a > 0
        )
        if holding:
            age += 1
            peak = max(peak, c)
            # 一字K线可令ATR归零：继续管理持仓，但不把跟踪止损收紧至收盘价。
            if isfinite(a) and a > 0:
                stop = max(stop, peak - trailing_atr * a)
            leave = c <= stop or age >= max_hold or (take_profit is not None and c >= take_profit)
            if leave:
                holding, wait = False, cooldown
                armed_level = None
            else:
                targets.iloc[i] = 1.0
            # 同根K线退出优先，不再立即开仓。
            continue
        if wait:
            wait -= 1
            continue
        if armed_level is not None:
            armed_age += 1
            if armed_age > retest_bars:
                armed_level = None
        if not ready:
            continue

        trending = c > row.ma and row.slope >= 0
        ranging = abs(row.slope) <= range_slope
        strong_close = h > bar_low and (c - bar_low) / (h - bar_low) >= 0.6
        enter, level, ceiling = False, row.support, None
        if strategy == "breakout":
            enter = trending and strong_close and c > row.resistance + buffer_atr * a
            level = row.resistance
        elif strategy == "bounce":
            enter = (
                ranging
                and strong_close
                and bar_low <= row.support + buffer_atr * a
                and c > row.support + buffer_atr * a
            )
            ceiling = row.resistance - buffer_atr * a
        elif strategy == "spring":
            old = (row.previous_support, row.previous_resistance, row.previous_atr)
            if all(isfinite(x) for x in old):
                level = row.previous_support
                enter = (
                    ranging
                    and row.previous_close < level - buffer_atr * row.previous_atr
                    and c > level + buffer_atr * a
                    and c > row.previous_close
                )
                ceiling = row.previous_resistance - buffer_atr * a
        else:
            if armed_level is not None:
                if c <= armed_level - stop_atr * a:
                    armed_level = None
                else:
                    enter = (
                        bar_low <= armed_level + buffer_atr * a
                        and c > armed_level + buffer_atr * a
                        and row.slope >= 0
                    )
                    level = armed_level
            # 突破当根只设观察位；回踩必须发生在后续有效K线。
            if (
                armed_level is None
                and trending
                and strong_close
                and c > row.resistance + buffer_atr * a
            ):
                armed_level, armed_age = row.resistance, 0

        if enter:
            candidate_stop = max(level - stop_atr * a, c - risk_atr * a)
            risk = c - candidate_stop
            reward_ok = ceiling is None or (ceiling - c) >= min_reward_risk * risk
            if risk > 0 and reward_ok:
                holding, stop, peak, take_profit, age = True, candidate_stop, c, ceiling, 0
                armed_level = None
                targets.iloc[i] = 1.0
    return targets.reindex(bars.index)


def sr_breakout(bars: pd.DataFrame, **params) -> pd.Series:
    """趋势压力突破，日线默认60根通道／120根趋势。"""
    return sr_target(bars, strategy="breakout", **params)


def sr_bounce(bars: pd.DataFrame, **params) -> pd.Series:
    """箱体支撑反弹；默认40根箱体、60根均线，要求初始盈亏比>=1.5。"""
    return sr_target(bars, strategy="bounce", **{"lookback": 40, "trend_period": 60, **params})


def sr_retest(bars: pd.DataFrame, **params) -> pd.Series:
    """趋势突破后回踩，旧压力位固定，最多等待10根有效K线。"""
    return sr_target(bars, strategy="retest", **params)


def sr_spring(bars: pd.DataFrame, **params) -> pd.Series:
    """箱底假跌破收复；默认40根箱体、60根均线，避免向下移动支撑后追买。"""
    return sr_target(bars, strategy="spring", **{"lookback": 40, "trend_period": 60, **params})
