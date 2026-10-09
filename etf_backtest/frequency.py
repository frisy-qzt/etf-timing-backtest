"""Session-aware right-labelled bars; auction is included in the first minute."""

import re
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.dataset as ds

from .data import CODE, event_adjusted_prices, load_events, normalize_units


def normalize_frequency(value: str) -> str:
    if value in ("daily", "1d", "D"):
        return "daily"
    if not isinstance(value, str) or not re.fullmatch(r"[1-9][0-9]*min", value):
        raise ValueError("Use daily or Nmin (1 <= N <= 120)")
    if not 1 <= int(value[:-3]) <= 120:
        raise ValueError("Minute interval must be between 1 and 120")
    return value


def minute_count(value: str) -> int:
    value = normalize_frequency(value)
    if value == "daily":
        raise ValueError("Expected minute frequency")
    return int(value[:-3])


def resample_minutes(minute: pd.DataFrame, daily: pd.DataFrame, frequency: str) -> pd.DataFrame:
    """Input units are CNY and shares. Missing constituent minutes invalidate a bar."""
    n = minute_count(frequency)
    if minute.index.has_duplicates or not minute.index.is_monotonic_increasing:
        raise ValueError("Minute data must have sorted unique timestamps")
    t = minute.index
    clock = t.hour * 60 + t.minute
    morning = (clock >= 570) & (clock <= 690)
    afternoon = (clock >= 781) & (clock <= 900)
    if not (morning | afternoon).all() or (t.second != 0).any():
        raise ValueError("Unexpected minute timestamp")
    base = np.where(morning, 570, 780)
    elapsed = np.maximum(clock - base, 1)
    end = base + np.minimum(((elapsed - 1) // n + 1) * n, 120)
    labels = t.normalize() + pd.to_timedelta(end, unit="min")
    grouped = minute.groupby(labels)
    d = grouped.agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        amount=("amount", "sum"),
        volume=("volume", "sum"),
    )
    count = grouped.size()
    offsets = [base + min(x, 120) for base in (570, 780) for x in range(n, 120 + n, n)]
    expected = pd.DatetimeIndex(
        [day + pd.Timedelta(minutes=x) for day in daily.index for x in offsets], name="datetime"
    )
    if len(d.index.difference(expected)):
        raise ValueError("Minute dates outside supplied trading calendar")
    d = d.reindex(expected)
    sizes = [
        min(n, 120 - (i * n)) + (1 if base == 570 and i == 0 else 0)
        for base in (570, 780)
        for i in range((120 + n - 1) // n)
    ]
    d["missing_bar"] = count.reindex(expected).fillna(0).to_numpy() != np.tile(sizes, len(daily))
    price = minute[["open", "high", "low", "close"]]
    good = np.isfinite(price).all(axis=1) & price.gt(0).all(axis=1)
    good &= minute.low.le(price[["open", "close"]].min(axis=1)) & minute.high.ge(
        price[["open", "close"]].max(axis=1)
    )
    good &= minute.volume.ge(0) & minute.amount.ge(0)
    constituents = good.groupby(labels).all().reindex(expected, fill_value=False)
    d["valid"] = ~d.missing_bar & constituents & d.volume.gt(0)
    d["frequency"] = frequency
    d["vwap"] = d.amount / d.volume.replace(0, np.nan)
    days = expected.normalize()
    d["event_factor"] = daily.event_factor.reindex(days).to_numpy()
    for field in ("limit_up", "limit_down", "suspended"):
        if field in daily:
            d[field] = daily[field].reindex(days).to_numpy()
    d["mark_close"] = np.nan
    at_close = expected.hour == 15
    d.loc[at_close, "mark_close"] = daily.valuation_close.reindex(days[at_close]).to_numpy()
    for field in ("open", "high", "low", "close"):
        d["signal_" + field] = d[field] * d.event_factor
    return d


def load_market_data(root: str | Path, frequency: str):
    """Read only 512100.SH using Arrow predicate pushdown; never modify sources."""
    root = Path(root)
    frequency = normalize_frequency(frequency)

    def read(freq):
        frame = (
            ds.dataset(root / freq / f"ETF_{freq}.parquet")
            .to_table(filter=ds.field("code") == CODE)
            .to_pandas()
        )
        if frame.datetime.duplicated().any():
            raise ValueError("Duplicate source timestamps")
        return normalize_units(frame.sort_values("datetime").set_index("datetime"), freq)

    events = load_events(root)
    daily = event_adjusted_prices(read("daily"), events)
    daily["valid"] = (
        daily.volume.gt(0)
        & daily.low.gt(0)
        & daily.low.le(daily[["open", "close"]].min(axis=1))
        & daily.high.ge(daily[["open", "close"]].max(axis=1))
    )
    daily["mark_close"] = daily.valuation_close
    return (
        daily if frequency == "daily" else resample_minutes(read("1min"), daily, frequency)
    ), events
