"""Offline, deterministic demo using invented prices; no market data required."""

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from etf_backtest import BacktestConfig, StrategyRun, run_strategy


def make_bars() -> pd.DataFrame:
    """Return synthetic daily bars in CNY/shares, without corporate actions.

    Business weekdays here are an invented calendar, not an exchange calendar.
    """
    rng = np.random.default_rng(42)
    index = pd.bdate_range("2024-01-01", periods=500, name="datetime")
    close = 10 * np.exp(np.cumsum(rng.normal(0.0003, 0.012, len(index))))
    open_price = np.r_[10.0, close[:-1]] * np.exp(rng.normal(0, 0.003, len(index)))
    bars = pd.DataFrame(
        {
            "open": open_price,
            "high": np.maximum(open_price, close) * 1.005,
            "low": np.minimum(open_price, close) * 0.995,
            "close": close,
            "volume": 10_000_000.0,
            "amount": close * 10_000_000.0,
            "frequency": "daily",
            "valid": True,
        },
        index=index,
    )
    for field in ("open", "high", "low", "close"):
        bars[f"signal_{field}"] = bars[field]
    return bars


def run_demo(output_root: str | Path = "output/demo") -> StrategyRun:
    """Write one report; repeated runs replace the same named report."""
    return run_strategy(
        "合成数据均线",
        "trend",
        frequency="daily",
        bars=make_bars(),
        events=pd.DataFrame(),
        output_root=output_root,
        params={"fast": 10, "slow": 30},
        config=BacktestConfig(start="2024-01-01", end="2025-12-31"),
        ic_window=20,
        group_window=60,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, default=Path("output/demo"))
    args = parser.parse_args()
    run = run_demo(args.output_root)
    print("合成数据演示：虚构行情，不能据此评估投资收益。")
    print(f"报告：{run.report_path}")


if __name__ == "__main__":
    main()
