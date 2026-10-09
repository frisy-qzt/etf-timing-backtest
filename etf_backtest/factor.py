"""单标的因子检验：时序 IC、Rank IC、ICIR、IC 衰减与分组多空对照。

检验一律使用未二值化的原始因子值。未来收益由事件复权收盘价构造，
并且只在有效K线之间累计，因此不会跨越停牌或缺失窗口。
"""

from dataclasses import dataclass

import numpy as np
import pandas as pd

DEFAULT_HORIZONS = (1, 2, 3, 5, 10, 20)
DEFAULT_QUANTILES = 5
METHOD_LABELS = {"pearson": "Pearson 相关", "spearman": "Spearman 秩相关"}


def bars_per_year(frequency: str) -> float:
    """年化换算的每年K线数：日频252根，分钟频按每交易日240分钟折算。"""
    if frequency == "daily":
        return 252.0
    return 252.0 * 240.0 / int(frequency[:-3])


def default_ic_window(frequency: str) -> int:
    """IC 独立窗口的默认样本数：日频 60 个（约三个月），分钟频 240 个（约1–5个交易日）。

    窗口过短时窗口 IC 会被短周期噪声主导，与全样本 IC 明显不同。
    """
    return 60 if frequency == "daily" else 240


def valid_price(bars: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    """事件复权收盘价与有效标记。"""
    valid = (
        bars.valid.fillna(False).astype(bool)
        if "valid" in bars
        else pd.Series(True, index=bars.index)
    )
    price = bars["signal_close"] if "signal_close" in bars else bars["close"]
    return price.astype(float).where(valid), valid


def forward_returns(bars: pd.DataFrame, horizons=DEFAULT_HORIZONS) -> pd.DataFrame:
    """未来 h 根有效K线的收益率；h 在有效K线序列上计数。"""
    price, _ = valid_price(bars)
    base = price.dropna()
    return pd.DataFrame(
        {int(h): (base.shift(-int(h)) / base - 1).reindex(bars.index) for h in horizons},
        index=bars.index,
    )


def _aligned(factor, bars, horizon: int) -> pd.DataFrame:
    """因子值与未来 horizon 根收益的对齐样本，两端缺失一律剔除。"""
    values = pd.Series(factor, dtype=float)
    if not values.index.equals(bars.index):
        values = values.reindex(bars.index)
    frame = pd.DataFrame(
        {
            "factor": values.to_numpy(dtype=float),
            "forward": forward_returns(bars, [horizon])[int(horizon)].to_numpy(dtype=float),
        },
        index=bars.index,
    )
    return frame.replace([np.inf, -np.inf], np.nan).dropna()


def _corr(x: pd.Series, y: pd.Series, method: str) -> float:
    if method == "spearman":
        x, y = x.rank(), y.rank()
    if len(x) < 3 or not x.std(ddof=1) or not y.std(ddof=1):
        return np.nan
    return float(x.corr(y))


def _block_ic(frame: pd.DataFrame, window: int, method: str, stride: int) -> pd.Series:
    """把非重叠样本切成等长窗口，每个窗口计算一次相关系数。

    窗口之间互不重叠，因此窗口 IC 彼此独立，t 值可直接按 √窗口数 折算；
    这等价于截面因子检验中“每个调仓期一个 IC”的时序版本。
    """
    sample = frame.iloc[:: max(int(stride), 1)]
    values, index = [], []
    for start in range(0, len(sample) - int(window) + 1, int(window)):
        block = sample.iloc[start : start + int(window)]
        values.append(_corr(block.factor, block.forward, method))
        index.append(block.index[-1])
    return pd.Series(values, index=pd.DatetimeIndex(index), dtype=float)


def ic_series(
    factor,
    bars,
    horizon: int = 1,
    *,
    window: int | None = None,
    method: str = "pearson",
    stride: int | None = None,
) -> pd.Series:
    """单一口径的独立窗口 IC 序列，索引为每个窗口的结束时刻。"""
    if method not in METHOD_LABELS:
        raise ValueError("method must be pearson or spearman")
    window = int(
        window or default_ic_window(bars.frequency.iloc[0] if "frequency" in bars else "daily")
    )
    frame = _aligned(factor, bars, horizon)
    return _block_ic(frame, window, method, stride or max(int(horizon), 1)).rename(
        f"{method}_{int(horizon)}"
    )


def ic_table(
    factor,
    bars,
    frequency: str,
    *,
    horizons=DEFAULT_HORIZONS,
    window: int | None = None,
    methods=("pearson", "spearman"),
    stride: int | None = None,
) -> pd.DataFrame:
    """按持有期与相关口径汇总 IC：均值、标准差、ICIR、t 值、正负占比与样本结构。

    每 h 根K线抽样一次，未来收益不重叠；IC 以 window 个非重叠样本为一个
    独立窗口计算，窗口数即 ICIR 与 t 值的样本数。年化 ICIR 按每年独立窗口数折算。
    """
    window = int(window or default_ic_window(frequency))
    rows = []
    for horizon in [int(h) for h in horizons]:
        frame = _aligned(factor, bars, horizon)
        step = int(stride) if stride else max(horizon, 1)
        sample = frame.iloc[::step]
        block_bars = int(window) * max(step, 1)
        per_year = bars_per_year(frequency) / block_bars
        for method in methods:
            ic = _block_ic(frame, window, method, step)
            valid = ic.dropna()
            mean = valid.mean() if len(valid) else np.nan
            sd = valid.std(ddof=1) if len(valid) > 1 else np.nan
            icir = mean / sd if np.isfinite(sd) and sd > 0 else np.nan
            rows.append(
                {
                    "horizon": horizon,
                    "method": method,
                    "window": int(window),
                    "block_bars": block_bars,
                    "blocks": int(len(valid)),
                    "observations": int(len(sample)),
                    "ic_mean": mean,
                    "ic_std": sd,
                    "icir": icir,
                    "icir_annual": icir * np.sqrt(per_year)
                    if np.isfinite(icir) and per_year > 0
                    else np.nan,
                    "t_stat": icir * np.sqrt(len(valid))
                    if np.isfinite(icir) and len(valid)
                    else np.nan,
                    "positive_ratio": float((valid > 0).mean()) if len(valid) else np.nan,
                    "full_sample_ic": _corr(sample.factor, sample.forward, method),
                }
            )
    return pd.DataFrame(rows)


def _nav_stats(returns: pd.Series, nav: pd.Series, years: float, per_year: float) -> dict:
    sd = returns.std(ddof=1)
    return {
        "annual_return": float(nav.iloc[-1] ** (1 / years) - 1)
        if years > 0 and nav.iloc[-1] > 0
        else np.nan,
        "volatility": float(sd * np.sqrt(per_year)) if np.isfinite(sd) else np.nan,
        "sharpe": float(returns.mean() / sd * np.sqrt(per_year))
        if np.isfinite(sd) and sd > 0
        else np.nan,
        "max_drawdown": float((nav / nav.cummax() - 1).min()),
        "final_nav": float(nav.iloc[-1]),
    }


def quantile_analysis(
    factor,
    bars,
    frequency: str,
    *,
    quantiles: int = DEFAULT_QUANTILES,
    holding: int = 1,
    window: int = 252,
    min_periods: int | None = None,
    min_samples: int = 20,
) -> tuple[pd.DataFrame, pd.DataFrame, float]:
    """因子分位分组对照：分组择时净值、多空组合净值与单调性。

    分位阈值只用当前及之前的样本（滚动窗口），不引入未来信息。
    每个分组是“仅在因子落入该组时持有标的”的择时组合；多空组合为
    最高组做多、最低组做空，其余时间空仓，属于统计对照而非可实盘组合。
    """
    quantiles, holding = int(quantiles), int(holding)
    if quantiles < 2:
        raise ValueError("quantiles must be at least 2")
    frame = _aligned(factor, bars, holding).iloc[:: max(holding, 1)]
    empty = (pd.DataFrame(), pd.DataFrame(), np.nan)
    if len(frame) < max(int(min_samples), quantiles * 2):
        return empty
    periods = int(min_periods or max(20, window // 4))
    periods = min(periods, len(frame))
    thresholds = [
        frame.factor.rolling(int(window), min_periods=periods).quantile(j / quantiles)
        for j in range(1, quantiles)
    ]
    ready = (
        pd.concat(thresholds, axis=1).notna().all(axis=1)
        if thresholds
        else pd.Series(True, index=frame.index)
    )
    bucket = pd.Series(1.0, index=frame.index)
    for threshold in thresholds:
        bucket = bucket + (frame.factor > threshold).astype(float)
    labels = bucket.where(ready)
    active = labels.notna()
    if active.sum() < min_samples:
        return empty
    years = ((frame.index[-1] - frame.index[0]).days + 1) / 365.25
    per_year = bars_per_year(frequency) / holding
    rows, navs = [], {}
    for group in range(1, quantiles + 1):
        held = (labels == group).fillna(False)
        returns = frame.forward.where(held, 0.0)
        nav = (1.0 + returns).cumprod()
        navs[f"分组{group}"] = nav
        rows.append(
            {
                "group": group,
                "samples": int(held.sum()),
                "share": float(held.sum() / active.sum()),
                "conditional_return": float(frame.forward[held].mean()) if held.any() else np.nan,
                "win_rate": float((frame.forward[held] > 0).mean()) if held.any() else np.nan,
                **_nav_stats(returns, nav, years, per_year),
            }
        )
    spread = frame.forward.where(labels == quantiles, 0.0) - frame.forward.where(labels == 1, 0.0)
    spread_nav = (1.0 + spread).cumprod()
    navs["多空组合"] = spread_nav
    traded = (labels == quantiles) | (labels == 1)
    rows.append(
        {
            "group": "long_short",
            "samples": int(traded.sum()),
            "share": float(traded.sum() / active.sum()),
            "conditional_return": float(spread[traded].mean()) if traded.any() else np.nan,
            "win_rate": float((spread[traded] > 0).mean()) if traded.any() else np.nan,
            **_nav_stats(spread, spread_nav, years, per_year),
        }
    )
    conditions = pd.Series({row["group"]: row["conditional_return"] for row in rows[:-1]}).dropna()
    monotonicity = (
        float(
            pd.Series(conditions.index.astype(float), index=conditions.index)
            .rank()
            .corr(conditions.rank())
        )
        if len(conditions) >= 3
        else np.nan
    )
    return pd.DataFrame(rows), pd.DataFrame(navs, index=frame.index), monotonicity


@dataclass
class FactorReport:
    """因子检验结果：IC 汇总、衰减曲线、分组对照与多空净值。"""

    name: str
    frequency: str
    horizons: tuple
    quantiles: int
    ic_window: int
    group_window: int
    holding: int
    ic: pd.DataFrame
    decay: pd.DataFrame
    groups: pd.DataFrame
    nav: pd.DataFrame
    monotonicity: float = np.nan

    @property
    def available(self) -> bool:
        """分组对照样本是否充足。"""
        return not self.groups.empty

    def summary(self) -> dict:
        """程序接口用的紧凑汇总：默认持有期为1根K线。"""
        first = self.ic[(self.ic.horizon == min(self.horizons))] if len(self.ic) else self.ic
        out = {
            "ic_window": self.ic_window,
            "quantiles": self.quantiles,
            "monotonicity": self.monotonicity,
        }
        for row in first.itertuples():
            out[f"{row.method}_ic"] = row.ic_mean
            out[f"{row.method}_icir"] = row.icir
            out[f"{row.method}_t"] = row.t_stat
        if self.available:
            spread = self.groups[self.groups.group == "long_short"]
            out["long_short_annual"] = (
                float(spread.annual_return.iloc[0]) if len(spread) else np.nan
            )
            out["long_short_final_nav"] = float(spread.final_nav.iloc[0]) if len(spread) else np.nan
        return out


def factor_report(
    factor,
    bars,
    frequency: str,
    *,
    name: str = "",
    horizons=DEFAULT_HORIZONS,
    quantiles: int = DEFAULT_QUANTILES,
    ic_window: int | None = None,
    group_window: int = 252,
    holding: int = 1,
    methods=("pearson", "spearman"),
) -> FactorReport:
    """生成完整因子检验结果；horizons 为未来收益的持有期（根K线）。"""
    horizons = tuple(int(h) for h in horizons)
    window = int(ic_window or default_ic_window(frequency))
    ic = ic_table(factor, bars, frequency, horizons=horizons, window=window, methods=methods)
    decay = (
        ic.pivot(index="horizon", columns="method", values="ic_mean") if len(ic) else pd.DataFrame()
    )
    groups, nav, monotonicity = quantile_analysis(
        factor, bars, frequency, quantiles=quantiles, holding=holding, window=group_window
    )
    return FactorReport(
        name=name,
        frequency=frequency,
        horizons=horizons,
        quantiles=int(quantiles),
        ic_window=int(window),
        group_window=int(group_window),
        holding=int(holding),
        ic=ic,
        decay=decay,
        groups=groups,
        nav=nav,
        monotonicity=monotonicity,
    )
