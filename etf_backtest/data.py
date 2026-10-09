"""Normalize local sources and build a causal, event-adjusted research view."""

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.dataset as ds

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


def prepare_data(root: str | Path) -> tuple[dict[str, pd.DataFrame], pd.DataFrame, dict]:
    root = Path(root)
    output = root / "data/research"
    output.mkdir(parents=True, exist_ok=True)
    events = load_events(root)
    raw, prepared = {}, {}
    fingerprints = {}
    for frequency in ["daily", "60min"]:
        path = root / frequency / f"ETF_{frequency}.parquet"
        frame = ds.dataset(path).to_table(filter=ds.field("code") == CODE).to_pandas()
        if frame.datetime.duplicated().any():
            raise ValueError(f"Duplicate {frequency} timestamps")
        frame = frame.sort_values("datetime").set_index("datetime")
        frame = frame.loc[:"2026-09-30"]
        raw[frequency] = frame
        d = normalize_units(frame, frequency)
        good_price = np.isfinite(d[["open", "high", "low", "close"]]).all(axis=1) & (
            d[["open", "high", "low", "close"]] > 0
        ).all(axis=1)
        ohlc = (
            (d.low <= d[["open", "close"]].min(axis=1))
            & (d.high >= d[["open", "close"]].max(axis=1))
            & (d.high >= d.low)
        )
        d["valid"] = good_price & ohlc & (d.volume > 0)
        d["missing_bar"] = False
        prepared[frequency] = d
        fingerprints[str(path.relative_to(root))] = {
            "bytes": path.stat().st_size,
            "mtime_ns": path.stat().st_mtime_ns,
        }
        fingerprints[str(path.relative_to(root))]["selected_rows_sha256"] = hashlib.sha256(
            pd.util.hash_pandas_object(frame, index=True).values.tobytes()
        ).hexdigest()
    hourly_repairs = []
    missing_days = raw["daily"].index.difference(raw["60min"].index.normalize().unique())
    for day in missing_days:
        path = root / "1min/ETF_1min.parquet"
        minute = (
            ds.dataset(path)
            .to_table(
                filter=(ds.field("code") == CODE)
                & (ds.field("datetime") >= day)
                & (ds.field("datetime") < day + pd.Timedelta(days=1))
            )
            .to_pandas()
        )
        if minute.empty:
            continue
        minute = minute.sort_values("datetime").set_index("datetime")
        rebuilt = normalize_units(aggregate_hourly(minute), "60min")
        rebuilt["valid"] = True
        rebuilt["missing_bar"] = False
        rebuilt["repaired_from_1min"] = True
        prepared["60min"] = pd.concat([prepared["60min"], rebuilt]).sort_index()
        minute.to_parquet(output / f"{CODE}_1min_evidence_{day:%Y%m%d}.parquet")
        fingerprints[f"1min/ETF_1min.parquet:{day:%Y%m%d}"] = {
            "selected_rows_sha256": hashlib.sha256(
                pd.util.hash_pandas_object(minute, index=True).values.tobytes()
            ).hexdigest()
        }
        hourly_repairs.append(
            {
                "date": str(day.date()),
                "source": "1min/ETF_1min.parquet",
                "minute_rows": len(minute),
                "rebuilt_bars": len(rebuilt),
                "amount_yuan": float(rebuilt.amount.sum()),
            }
        )
    # The CSV is an independent check of the generated daily Parquet.
    source_files = sorted((root / "ETF日线").glob(f"*/{CODE}.csv"))
    original = (
        pd.concat([pd.read_csv(p, parse_dates=["datetime"]) for p in source_files])
        .sort_values("datetime")
        .set_index("datetime")
    )
    source_match = bool(
        np.allclose(
            original[["open", "high", "low", "close", "amount", "volume"]],
            raw["daily"][["open", "high", "low", "close", "amount", "volume"]],
            equal_nan=True,
        )
    )
    if not source_match:
        raise ValueError("Daily Parquet differs from original CSV")
    daily = event_adjusted_prices(prepared["daily"], events)
    hourly = complete_hourly_grid(prepared["60min"], daily)
    days = hourly.index.normalize()
    for field in ["event_factor", "limit_up", "limit_down"]:
        if field in daily:
            hourly[field] = daily[field].reindex(days).to_numpy()
    for field in ["open", "high", "low", "close"]:
        hourly["signal_" + field] = hourly[field] * hourly.event_factor
    daily["mark_close"] = daily.valuation_close.where(~daily.valid)
    prepared = {"daily": daily, "60min": hourly}
    # Source factors are evidence only. Their affine source convention is not used
    # as a future-dependent trading signal, nor mistaken for corporate actions.
    factors = pd.DataFrame(index=daily.index)
    repairs = []
    for version in ["qfq", "hfq"]:
        factor_path = root / f"基金_复权因子/{version}/{CODE}.csv"
        fingerprints[str(factor_path.relative_to(root))] = {
            "sha256": hashlib.sha256(factor_path.read_bytes()).hexdigest()
        }
        f = pd.read_csv(factor_path)
        f.columns = ["code", "date", "factor"]
        f["date"] = pd.to_datetime(f.date.astype(str), format="%Y%m%d")
        factors[version + "_source"] = f.set_index("date").factor.reindex(daily.index)
        fixed = factors[version + "_source"].copy()
        suspension = pd.Timestamp("2022-09-02")
        if pd.isna(fixed.loc[suspension]):
            # The source quote on suspension day is pre-consolidation and stale.
            fixed.loc[suspension] = fixed.loc[:"2022-09-01"].iloc[-1]
            repairs.append(
                {
                    "date": str(suspension.date()),
                    "field": version,
                    "value": fixed.loc[suspension],
                    "basis": "carry prior factor for stale pre-split source quote; no trade or new signal",
                }
            )
        fixed.loc[pd.Timestamp("2026-09-30")] = 1.0
        repairs.append(
            {
                "date": "2026-09-30",
                "field": version,
                "value": 1.0,
                "basis": "explicit user override",
            }
        )
        if fixed.isna().any() or (fixed <= 0).any():
            raise ValueError(f"Unresolved source factor issue {version}")
        factors[version + "_research_reference"] = fixed
    factors["event_forward_factor"] = daily.event_factor
    factors.to_csv(output / "factor_audit.csv", index_label="date")
    pd.DataFrame(repairs).to_csv(output / "repairs.csv", index=False)
    sums = hourly.groupby(days)[["volume", "amount"]].sum(min_count=1)
    recon = daily[["volume", "amount"]].join(sums, rsuffix="_hourly")
    for field in ["volume", "amount"]:
        recon[field + "_difference_pct"] = (recon[field + "_hourly"] / recon[field] - 1) * 100
    recon.to_csv(output / "turnover_reconciliation.csv")
    anomalies = []
    for frequency, d in prepared.items():
        invalid_vwap = (d.volume > 0) & ((d.vwap < d.low - 0.001) | (d.vwap > d.high + 0.001))
        for label, flags in [
            ("missing", d.missing_bar),
            ("zero_volume", d.volume.eq(0)),
            ("vwap_outside", invalid_vwap),
        ]:
            for ts in d.index[flags]:
                anomalies.append({"frequency": frequency, "timestamp": ts, "issue": label})
        d.to_parquet(output / f"{CODE}_{frequency}.parquet")
    pd.DataFrame(anomalies, columns=["frequency", "timestamp", "issue"]).to_csv(
        output / "quality_issues.csv", index=False
    )
    event_path = output / "corporate_actions.csv"
    fingerprints["data/research/corporate_actions.csv"] = {
        "sha256": hashlib.sha256(event_path.read_bytes()).hexdigest()
    }
    report = {
        "symbol": CODE,
        "start": str(daily.index.min().date()),
        "end": str(daily.index.max().date()),
        "daily_rows": len(daily),
        "hourly_source_rows": len(raw["60min"]),
        "hourly_grid_rows": len(hourly),
        "daily_matches_original_csv": source_match,
        "verified_events": len(events),
        "units": {
            "daily_source_amount": "thousand CNY",
            "minute_source_amount": "CNY",
            "source_volume": "100 shares",
            "research_amount": "CNY",
            "research_volume": "shares",
        },
        "missing_hourly_dates": sorted(
            set(str(x.date()) for x in hourly.index[hourly.missing_bar].normalize())
        ),
        "amount_residual_over_0_1_percent_days": int(
            recon.amount_difference_pct.abs().gt(0.1).sum()
        ),
        "hourly_repairs": hourly_repairs,
        "issues": pd.DataFrame(anomalies)
        .groupby(["frequency", "issue"])
        .size()
        .rename("count")
        .reset_index()
        .to_dict("records"),
        "source_fingerprints": fingerprints,
        "assumptions": [
            "Cash dividend entitlement retained at full floating precision; actual cent rounding is unspecified.",
            "Suspended split-day valuation uses previous traded price / share ratio, not future-published NAV.",
            "VWAP is a price benchmark, not a guarantee of execution capacity; participation is reported.",
            "Missing intraday closing valuation may use contemporaneous daily close; no intraday signal/fill is fabricated.",
            "Source factor overrides are audit references; trading signals use causal event-forward adjustment.",
        ],
    }
    (output / "quality_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    return prepared, events, report
