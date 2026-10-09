"""策略调用入口：仅保存一个中文 HTML，完整回测结果保留在内存。"""

import hashlib
import inspect
import platform
import re
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from importlib.metadata import version
from pathlib import Path
from typing import Callable, Sequence
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from .engine import BacktestConfig, BacktestResult, run_backtest
from .factor import DEFAULT_HORIZONS, DEFAULT_QUANTILES, FactorReport, factor_report
from .frequency import load_market_data, normalize_frequency
from .metrics import performance
from .strategies import DEFAULTS, RAW_FACTOR_LABELS, RAW_FACTOR_NAMES, raw_factor, signal


@dataclass
class StrategyRun:
    result: BacktestResult
    metrics: dict
    report_path: Path
    factor: FactorReport | None = None

    @property
    def output_dir(self) -> Path:
        """返回报告所在目录；完整文件路径为 report_path。"""
        return self.report_path.parent


def run_strategy(
    factor_name: str,
    strategy: str | Callable | pd.Series,
    *,
    frequency: str = "daily",
    bars: pd.DataFrame | None = None,
    events: pd.DataFrame | None = None,
    data_root: str | Path | None = None,
    output_root: str | Path = "output/factors",
    config: BacktestConfig | None = None,
    params: dict | None = None,
    intraday_price: str | None = None,
    factor: pd.Series | Callable | None = None,
    factor_analysis: bool = True,
    horizons: Sequence[int] = DEFAULT_HORIZONS,
    quantiles: int = DEFAULT_QUANTILES,
    ic_window: int | None = None,
    group_window: int = 252,
) -> StrategyRun:
    """策略函数签名：strategy(bars, **params) -> 对齐的 0/1/NaN 序列。

    预热数据不计入收益；仅区间内信号产生订单。成功后原子替换
    output_root / f'{factor_name}report.html'，失败保留此前成功报告。
    因子检验使用未二值化的原始因子值：内置策略自动取其连续因子，
    也可通过 factor 传入自定义因子序列或 factor(bars, **params) 函数。
    """
    if not isinstance(factor_name, str) or not re.fullmatch(
        r"[\w\-\u4e00-\u9fff]{1,80}", factor_name
    ):
        raise ValueError("因子名须为1–80个中文、字母、数字、下划线或连字符")
    frequency = normalize_frequency(frequency)
    base_config = config or BacktestConfig()
    c = replace(
        base_config, intraday_price=intraday_price or base_config.intraday_price or "extreme"
    )
    report_path = Path(output_root).resolve() / f"{factor_name}report.html"
    metadata = dict(
        factor_name=factor_name,
        frequency=frequency,
        params=params or {},
        config=asdict(c),
        generated_at=datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S"),
    )
    if bars is None:
        if data_root is None:
            raise ValueError("请提供 bars 或 data_root")
        if events is not None:
            raise ValueError("本地读取使用已核验事件；自定义事件请与 bars 一起传入")
        bars, events = load_market_data(data_root, frequency)
    if not isinstance(bars.index, pd.DatetimeIndex) or bars.empty:
        raise ValueError("行情须使用非空 DatetimeIndex")
    if "frequency" not in bars or not bars.frequency.eq(frequency).all():
        raise ValueError("行情的 frequency 列须与调用频率一致")
    # 自定义策略只看到评估截止日及之前的数据；先核验原始目标索引再裁剪。
    if isinstance(strategy, pd.Series) and not strategy.index.equals(bars.index):
        raise ValueError(
            "Supplied targets must exactly align with original bars：目标索引须完全对齐"
        )
    if isinstance(factor, pd.Series) and not factor.index.equals(bars.index):
        raise ValueError(
            "Supplied factor must exactly align with original bars：因子索引须完全对齐"
        )
    bars = bars.loc[bars.index < pd.Timestamp(c.end) + pd.Timedelta(days=1)].copy()
    if isinstance(strategy, str):
        targets = signal(bars, strategy, params)
        metadata["strategy"] = strategy
        metadata["params"] = {**DEFAULTS.get(strategy, {}), **(params or {})}
    elif isinstance(strategy, pd.Series):
        targets = strategy.loc[bars.index]
        metadata["strategy"] = "supplied_targets"
    elif callable(strategy):
        try:
            source = inspect.getsource(strategy)
        except (TypeError, OSError):
            source = None
        if source is not None:
            metadata["strategy_sha256"] = hashlib.sha256(source.encode()).hexdigest()
        targets = strategy(bars.copy(), **(params or {}))
        metadata["strategy"] = getattr(strategy, "__qualname__", type(strategy).__name__)
    else:
        raise TypeError("策略须为内置名称、函数或目标序列")
    if not isinstance(targets, pd.Series):
        raise TypeError("策略须返回 pandas.Series")
    # 因子检验用未二值化的原始因子：显式传入优先，其次取内置策略的连续因子。
    values, factor_source = None, None
    if factor is not None:
        if isinstance(factor, pd.Series):
            values, factor_source = (
                factor.reindex(bars.index).astype(float),
                "外部因子序列（未二值化）",
            )
        elif callable(factor):
            try:
                code = inspect.getsource(factor)
            except (TypeError, OSError):
                code = None
            if code is not None:
                metadata["factor_source_sha256"] = hashlib.sha256(code.encode()).hexdigest()
            values = factor(bars.copy(), **(params or {}))
            factor_source = f"自定义因子函数（未二值化）：{getattr(factor, '__qualname__', type(factor).__name__)}"
        else:
            raise TypeError("因子须为 pandas.Series 或可调用对象")
    elif isinstance(strategy, str) and strategy in RAW_FACTOR_NAMES:
        values = raw_factor(bars, strategy, params)
        factor_source = f"内置策略原始因子（未二值化）：{RAW_FACTOR_LABELS[strategy]}"
    if values is not None and not isinstance(values, pd.Series):
        raise TypeError("因子须返回 pandas.Series")
    result = run_backtest(bars, targets, c, events)
    benchmark = run_backtest(bars, signal(bars, "buy_hold"), c, events)
    cash = run_backtest(bars, signal(bars, "cash"), c, events)
    metrics = performance(result)
    limits = all(x in bars for x in ("limit_up", "limit_down"))
    interval_bars = bars.loc[
        (bars.index >= result.ledger.index[0].normalize()) & (bars.index <= result.ledger.index[-1])
    ]
    limit_coverage = (
        (
            np.isfinite(interval_bars[["limit_up", "limit_down"]]).all(axis=1)
            & interval_bars[["limit_up", "limit_down"]].gt(0).all(axis=1)
        ).mean()
        if limits
        else 0.0
    )
    diagnostics = {
        "rows": len(bars),
        "evaluation_rows": len(result.ledger),
        "invalid_bars": int((~bars.valid).sum()) if "valid" in bars else None,
        "missing_bars": int(bars.missing_bar.sum()) if "missing_bar" in bars else None,
        "order_reasons": result.orders.reason.value_counts().to_dict(),
        "limit_price_coverage": float(limit_coverage),
        "events_supplied": events is not None,
        "price_model": c.intraday_price if frequency != "daily" else "next_open",
    }
    metadata.update(
        rows=len(result.ledger),
        python=platform.python_version(),
        dependencies={name: version(name) for name in ("numpy", "pandas", "pyarrow", "plotly")},
        data_sha256=hashlib.sha256(
            pd.util.hash_pandas_object(bars, index=True).values.tobytes()
        ).hexdigest(),
        signal_sha256=hashlib.sha256(
            pd.util.hash_pandas_object(targets, index=True).values.tobytes()
        ).hexdigest(),
        events_sha256=hashlib.sha256(
            pd.util.hash_pandas_object(events, index=True).values.tobytes()
        ).hexdigest()
        if events is not None
        else None,
    )
    from .report import export_strategy_report

    factor_stats = None
    if values is not None and factor_analysis:
        # 因子检验区间与回测评估区间一致，便于与绩效指标对照。
        first = pd.Timestamp(c.start) if c.start else bars.index.min().normalize()
        window = bars.loc[
            (bars.index >= first) & (bars.index < pd.Timestamp(c.end) + pd.Timedelta(days=1))
        ]
        factor_stats = factor_report(
            values,
            window,
            frequency,
            name=factor_source or factor_name,
            horizons=horizons,
            quantiles=quantiles,
            ic_window=ic_window,
            group_window=group_window,
        )
        metadata["factor_source"] = factor_source
        metadata["factor_sha256"] = hashlib.sha256(
            pd.util.hash_pandas_object(values.reindex(window.index), index=True).values.tobytes()
        ).hexdigest()
    export_strategy_report(
        result,
        benchmark,
        bars,
        report_path,
        factor_name,
        frequency,
        cash=cash,
        metadata=metadata,
        diagnostics=diagnostics,
        factor=factor_stats,
    )
    return StrategyRun(result, metrics, report_path, factor_stats)
