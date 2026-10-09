"""Reproducible long/cash ETF timing research."""

from .api import StrategyRun, run_strategy
from .engine import BacktestConfig, BacktestResult, run_backtest
from .factor import (
    FactorReport,
    factor_report,
    forward_returns,
    ic_series,
    ic_table,
    quantile_analysis,
)
from .frequency import load_market_data, resample_minutes
from .strategies import raw_factor

__all__ = [
    "BacktestConfig",
    "BacktestResult",
    "run_backtest",
    "StrategyRun",
    "run_strategy",
    "load_market_data",
    "resample_minutes",
    "FactorReport",
    "factor_report",
    "forward_returns",
    "ic_series",
    "ic_table",
    "quantile_analysis",
    "raw_factor",
]
