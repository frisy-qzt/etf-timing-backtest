"""Run from an installed package: python examples/new_strategy.py --data-root /path/to/data."""

import argparse

import pandas as pd

from etf_backtest import BacktestConfig, run_strategy


def moving_average_target(bars: pd.DataFrame, fast: int = 20, slow: int = 60) -> pd.Series:
    """0/1 目标仓位：快均线在慢均线上方时持有。"""
    if not 0 < fast < slow:
        raise ValueError("Require 0 < fast < slow")
    close = bars.loc[bars.valid, "signal_close"]
    long_mean = close.rolling(slow).mean()
    target = (close.rolling(fast).mean() > long_mean).astype(float).where(long_mean.notna(), 0.0)
    return target.reindex(bars.index)


def moving_average_factor(bars: pd.DataFrame, fast: int = 20, slow: int = 60) -> pd.Series:
    """未二值化的连续因子：快慢均线相对价差，交给 IC／分组检验。"""
    if not 0 < fast < slow:
        raise ValueError("Require 0 < fast < slow")
    close = bars.loc[bars.valid, "signal_close"]
    return (close.rolling(fast).mean() / close.rolling(slow).mean() - 1).reindex(bars.index)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", required=True)
    parser.add_argument("--frequency", default="5min")
    parser.add_argument("--start", default="2022-01-01")
    parser.add_argument("--end", default="2026-09-30")
    parser.add_argument("--output-root", default="output/factors")
    args = parser.parse_args()
    run = run_strategy(
        "自定义双均线",
        moving_average_target,
        frequency=args.frequency,
        data_root=args.data_root,
        output_root=args.output_root,
        params={"fast": 20, "slow": 60},
        factor=moving_average_factor,
        config=BacktestConfig(start=args.start, end=args.end),
    )
    print(f"回测完成，中文报告已保存：{run.report_path}")
    print(f"因子检验：{run.factor.summary()}")
