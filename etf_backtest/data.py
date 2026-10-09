"""Normalize market data and construct causal event-adjusted prices."""

from pathlib import Path

import numpy as np
import pandas as pd

from .engine import validate_events

CODE = "512100.SH"


def normalize_units(frame: pd.DataFrame, frequency: str) -> pd.DataFrame:
    if frequency not in ("daily", "60min", "1min"):
        raise ValueError(frequency)
    d = frame.copy()
    d["amount_source"] = d.amount
    d["volume_source"] = d.volume
    d["amount"] = d.amount.astype(float) * (1000 if frequency == "daily" else 1)
    d["volume"] = d.volume.astype(float) * 100
    d["vwap"] = d.amount / d.volume.replace(0, np.nan)
    d["frequency"] = frequency
    return d


def load_events(root: Path) -> pd.DataFrame:
    e = pd.read_csv(root / "data/research/corporate_actions.csv")
    e = e.rename(
        columns={
            "event_id": "id",
            "event_type": "kind",
            "effective_date": "ex_date",
            "share_ratio": "ratio",
        }
    )
    e["kind"] = e.kind.replace({"consolidation": "split", "cash_dividend": "dividend"})
    e["rounding"] = e.rounding.replace({"ceil_total_account_shares": "ceil"})
    validate_events(e)
    return e


def event_adjusted_prices(daily: pd.DataFrame, events: pd.DataFrame) -> pd.DataFrame:
    """Forward adjustment uses only effective events, never future dividends.

    The factor starts at one. Split f*=ratio; cash distribution f*=P/(P-D).
    Suspended stale quotes are replaced ONLY in the valuation/signal view.
    """
    d = daily.copy()
    event_map = {}
    for e in validate_events(events):
        event_map.setdefault(e["ex_date"], []).append(e)
    factor, prior = 1.0, np.nan
    factors, marks = [], []
    for date, row in d.iterrows():
        for e in event_map.get(date.normalize(), []):
            if e["kind"] == "split":
                factor *= e["ratio"]
                prior /= e["ratio"]
            else:
                if not np.isfinite(prior) or prior <= e["cash_per_share"]:
                    raise ValueError("Cannot calculate dividend adjustment")
                factor *= prior / (prior - e["cash_per_share"])
                prior -= e["cash_per_share"]
        if row.volume > 0 and np.isfinite(row.close) and row.close > 0:
            prior = row.close
        elif not np.isfinite(prior):
            prior = row.close
        factors.append(factor)
        marks.append(prior)
    d["event_factor"] = factors
    d["valuation_close"] = marks
    for field in ["open", "high", "low", "close"]:
        d["signal_" + field] = d[field].where(d.volume > 0, d.valuation_close) * d.event_factor
    return d


def complete_hourly_grid(hourly: pd.DataFrame, daily: pd.DataFrame) -> pd.DataFrame:
    expected = pd.DatetimeIndex(
        [
            day + pd.Timedelta(hours=h, minutes=m)
            for day in daily.index
            for h, m in [(10, 30), (11, 30), (14, 0), (15, 0)]
        ],
        name="datetime",
    )
    extras = hourly.index.difference(expected)
    if len(extras):
        raise ValueError(f"Unexpected hourly timestamps: {extras[:5]}")
    d = hourly.reindex(expected)
    d["missing_bar"] = ~expected.isin(hourly.index)
    d["valid"] = d.valid.fillna(False).astype(bool)
    d["mark_close"] = np.nan
    missing_end = d.missing_bar & (d.index.hour == 15)
    value_field = "valuation_close" if "valuation_close" in daily else "close"
    d.loc[missing_end, "mark_close"] = [
        daily.loc[x.normalize(), value_field] for x in d.index[missing_end]
    ]
    d["frequency"] = "60min"
    return d


def aggregate_hourly(minute: pd.DataFrame) -> pd.DataFrame:
    """Source minute bars are right-labelled; 09:30 auction belongs to first bar."""
    if minute.empty or minute.index.has_duplicates or minute.index.normalize().nunique() != 1:
        raise ValueError("Need exactly one complete unique minute session")
    day = minute.index[0].normalize()
    expected = pd.date_range(
        day + pd.Timedelta(hours=9, minutes=30),
        day + pd.Timedelta(hours=11, minutes=30),
        freq="min",
    ).append(
        pd.date_range(
            day + pd.Timedelta(hours=13, minutes=1), day + pd.Timedelta(hours=15), freq="min"
        )
    )
    if not minute.index.sort_values().equals(expected):
        raise ValueError("Incomplete minute session; no synthetic hourly repair")
    labels = []
    for ts in minute.index:
        clock = ts.hour * 60 + ts.minute
        h, m = (
            (10, 30)
            if clock <= 630
            else (11, 30)
            if clock <= 690
            else (14, 0)
            if clock <= 840
            else (15, 0)
        )
        labels.append(day + pd.Timedelta(hours=h, minutes=m))
    return minute.groupby(pd.DatetimeIndex(labels, name="datetime")).agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        volume=("volume", "sum"),
        amount=("amount", "sum"),
    )
