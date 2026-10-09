"""Causal signal templates; no trade lag here (the execution engine owns it)."""

import numpy as np
import pandas as pd

DEFAULTS = {
    "trend": {"fast": 20, "slow": 60},
    "breakout": {"entry": 40, "exit": 20},
    "momentum": {"lookback": 60, "threshold": 0.0},
    "mean_reversion": {"lookback": 20, "entry_z": -1.5, "exit_z": 0.0},
}
LABELS = {
    "trend": "均线趋势",
    "breakout": "通道突破",
    "momentum": "时间序列动量",
    "mean_reversion": "均值回归",
    "buy_hold": "买入持有",
    "cash": "始终空仓",
}
# 原始因子口径：连续值交给 IC／分组检验，方向为“数值越大越看多”。
# 均值回归是负向因子，IC 预期为负；买入持有与始终空仓没有因子。
RAW_FACTOR_LABELS = {
    "trend": "快慢均线相对价差（正向因子）",
    "breakout": "通道位置（收盘价在滚动高低区间中的位置，正向因子）",
    "momentum": "区间动量（回看期收益，正向因子）",
    "mean_reversion": "均值回归标准分（负向因子）",
}
RAW_FACTOR_NAMES = tuple(RAW_FACTOR_LABELS)


def signal(bars: pd.DataFrame, name: str, params: dict | None = None) -> pd.Series:
    p = {**DEFAULTS.get(name, {}), **(params or {})}
    original_index = bars.index
    bars = bars.loc[bars.valid]
    close = bars.signal_close
    if name == "buy_hold":
        out = pd.Series(1.0, index=bars.index)
    elif name == "cash":
        out = pd.Series(0.0, index=bars.index)
    elif name == "trend":
        if not 0 < p["fast"] < p["slow"]:
            raise ValueError("trend needs 0 < fast < slow")
        fast, slow = close.rolling(p["fast"]).mean(), close.rolling(p["slow"]).mean()
        out = (fast > slow).astype(float).where(slow.notna(), 0.0)
    elif name == "momentum":
        ret = close / close.shift(p["lookback"]) - 1
        out = (ret > p["threshold"]).astype(float).where(ret.notna(), 0.0)
    elif name in ("breakout", "mean_reversion"):
        if name == "breakout":
            hi = bars.signal_high.where(bars.valid).rolling(p["entry"]).max().shift(1)
            lo = bars.signal_low.where(bars.valid).rolling(p["exit"]).min().shift(1)
            enter, leave = close > hi, close < lo
        else:
            avg, sd = close.rolling(p["lookback"]).mean(), close.rolling(p["lookback"]).std()
            z = (close - avg) / sd.replace(0, np.nan)
            enter, leave = z < p["entry_z"], z >= p["exit_z"]
        switches = pd.Series(np.nan, index=bars.index)
        switches.loc[enter] = 1.0
        switches.loc[leave] = 0.0
        out = switches.ffill().fillna(0.0)
    else:
        raise ValueError(f"Unknown strategy {name}")
    return out.reindex(original_index).rename("target")


def raw_factor(bars: pd.DataFrame, name: str, params: dict | None = None) -> pd.Series | None:
    """内置策略对应的连续因子值（未二值化），暖机期为 NaN。

    与 signal() 使用同一组参数和同一批有效K线，因此因子检验与
    0/1 回测口径一致；买入持有与始终空仓没有因子，返回 None。
    """
    if name in ("buy_hold", "cash"):
        return None
    if name not in RAW_FACTOR_NAMES:
        raise ValueError(f"Unknown factor {name}")
    p = {**DEFAULTS.get(name, {}), **(params or {})}
    original_index = bars.index
    v = bars.loc[bars.valid]
    close = v.signal_close
    if name == "trend":
        if not 0 < p["fast"] < p["slow"]:
            raise ValueError("trend needs 0 < fast < slow")
        out = close.rolling(p["fast"]).mean() / close.rolling(p["slow"]).mean() - 1
    elif name == "momentum":
        out = close / close.shift(p["lookback"]) - 1
    elif name == "breakout":
        hi = v.signal_high.rolling(p["entry"]).max().shift(1)
        lo = v.signal_low.rolling(p["exit"]).min().shift(1)
        out = (close - lo) / (hi - lo).replace(0, np.nan)
    else:
        avg, sd = close.rolling(p["lookback"]).mean(), close.rolling(p["lookback"]).std()
        out = (close - avg) / sd.replace(0, np.nan)
    return out.replace([np.inf, -np.inf], np.nan).reindex(original_index).rename(f"{name}_factor")


def parameter_grid(name: str) -> list[dict]:
    if name == "trend":
        return [{"fast": f, "slow": s} for f in [10, 20, 30] for s in [40, 60, 90] if f < s]
    if name == "breakout":
        return [{"entry": n, "exit": m} for n in [20, 40, 60] for m in [10, 20] if m < n]
    if name == "momentum":
        return [{"lookback": n, "threshold": t} for n in [20, 60, 120] for t in [0.0, 0.02]]
    if name == "mean_reversion":
        return [
            {"lookback": n, "entry_z": z, "exit_z": 0.0}
            for n in [10, 20, 40]
            for z in [-1.0, -1.5, -2.0]
        ]
    raise ValueError(name)
