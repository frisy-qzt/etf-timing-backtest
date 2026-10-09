"""Independent range experiments and chronological parameter selection."""

import json
from dataclasses import asdict, replace
from pathlib import Path

import numpy as np
import pandas as pd

from .engine import BacktestConfig, run_backtest
from .metrics import performance, period_returns
from .strategies import DEFAULTS, parameter_grid, signal

RANGES = {"full": ("2016-11-04", "2026-09-30"), "since_2022": ("2022-01-01", "2026-09-30")}


def split_dates(index: pd.DatetimeIndex) -> dict[str, tuple[str, str]]:
    days = index.normalize().unique().sort_values()
    if len(days) < 10:
        raise ValueError("At least 10 dates required for chronological split")
    a, b = int(len(days) * 0.6), int(len(days) * 0.8)
    return {
        name: (str(days[left].date()), str(days[r - 1].date()))
        for name, left, r in [("train", 0, a), ("validation", a, b), ("test", b, len(days))]
    }


def export_result(result, path: Path):
    path.mkdir(parents=True, exist_ok=True)
    for name in ["ledger", "orders", "fills", "trades", "actions"]:
        frame = getattr(result, name)
        frame.to_parquet(path / f"{name}.parquet", index=(name == "ledger"))
        frame.to_csv(path / f"{name}.csv", index=(name == "ledger"))
    for period, label in [("M", "monthly_returns"), ("Y", "annual_returns")]:
        period_returns(result, period).rename("return").to_csv(path / f"{label}.csv")
    (path / "config.json").write_text(json.dumps(asdict(result.config), indent=2))
    pd.DataFrame([performance(result)]).to_csv(path / "metrics.csv", index=False)


def score(metrics):
    value = metrics["sharpe"]
    return float(value) if np.isfinite(value) else -1e9


def run_matrix(data, events, output: Path, config=None, ranges=None, fixed_parameters=None):
    config = config or BacktestConfig()
    ranges = ranges or RANGES
    results, summary = {}, []
    for version, (start, end) in ranges.items():
        for frequency, bars in data.items():
            for strategy in [*DEFAULTS, "buy_hold", "cash"]:
                c = replace(config, start=start, end=end)
                result = run_backtest(
                    bars, signal(bars, strategy, (fixed_parameters or {}).get(strategy)), c, events
                )
                key = (version, frequency, strategy)
                results[key] = result
                export_result(result, output / version / frequency / strategy)
                summary.append(
                    {
                        "version": version,
                        "frequency": frequency,
                        "strategy": strategy,
                        **performance(result),
                    }
                )
            print(f"Fixed backtests: {version}/{frequency}", flush=True)
    table = pd.DataFrame(summary)
    table.to_csv(output / "summary.csv", index=False)
    return results, table


def parameter_research(data, events, output: Path, config=None, ranges=None, fixed_parameters=None):
    """Train shortlist, validation selection, then exactly one test per strategy.

    Walk-forward folds reset capital/position; compounded fold returns are an
    evaluation index, not a continuously rebalanced live portfolio.
    """
    config = config or BacktestConfig()
    ranges = ranges or RANGES
    experiments, selections, folds, sensitivity, capacity = [], [], [], [], []
    oos_results, fold_results = {}, {}
    for version, (start, end) in ranges.items():
        for frequency, bars in data.items():
            range_bars = bars.loc[start:end]
            splits = split_dates(range_bars.index)
            for strategy in DEFAULTS:
                candidates = parameter_grid(strategy)
                targets = [signal(bars, strategy, p) for p in candidates]
                train_scores = []
                for i, p in enumerate(candidates):
                    s, e = splits["train"]
                    r = run_backtest(bars, targets[i], replace(config, start=s, end=e), events)
                    m = performance(r)
                    train_scores.append(score(m))
                    experiments.append(
                        {
                            "version": version,
                            "frequency": frequency,
                            "strategy": strategy,
                            "stage": "train",
                            "candidate": i,
                            "params": json.dumps(p, sort_keys=True),
                            **m,
                        }
                    )
                shortlist = sorted(range(len(candidates)), key=lambda i: (-train_scores[i], i))[:3]
                validation_scores = {}
                for i in shortlist:
                    s, e = splits["validation"]
                    r = run_backtest(bars, targets[i], replace(config, start=s, end=e), events)
                    m = performance(r)
                    validation_scores[i] = score(m)
                    experiments.append(
                        {
                            "version": version,
                            "frequency": frequency,
                            "strategy": strategy,
                            "stage": "validation",
                            "candidate": i,
                            "params": json.dumps(candidates[i], sort_keys=True),
                            **m,
                        }
                    )
                best = sorted(shortlist, key=lambda i: (-validation_scores[i], i))[0]
                s, e = splits["test"]
                result = run_backtest(bars, targets[best], replace(config, start=s, end=e), events)
                key = (version, frequency, strategy)
                oos_results[key] = result
                selections.append(
                    {
                        "version": version,
                        "frequency": frequency,
                        "strategy": strategy,
                        "params": json.dumps(candidates[best], sort_keys=True),
                        "train_start": splits["train"][0],
                        "train_end": splits["train"][1],
                        "validation_start": splits["validation"][0],
                        "validation_end": splits["validation"][1],
                        **performance(result),
                    }
                )
                export_result(result, output / version / frequency / "oos" / strategy)
                # A window may start mid-year for the full-history version; require
                # three calendar years inside the requested range before testing.
                first = pd.Timestamp(start)
                for year in range(first.year + 3, pd.Timestamp(end).year + 1):
                    train_start, train_end = (
                        pd.Timestamp(year - 3, 1, 1),
                        pd.Timestamp(year - 1, 12, 31),
                    )
                    if train_start < first:
                        continue
                    test_start, test_end = (
                        pd.Timestamp(year, 1, 1),
                        min(pd.Timestamp(year, 12, 31), pd.Timestamp(end)),
                    )
                    if not len(range_bars.loc[str(test_start.date()) : str(test_end.date())]):
                        continue
                    scores = []
                    for i in range(len(candidates)):
                        r = run_backtest(
                            bars,
                            targets[i],
                            replace(
                                config, start=str(train_start.date()), end=str(train_end.date())
                            ),
                            events,
                        )
                        scores.append(score(performance(r)))
                    chosen = max(range(len(scores)), key=lambda i: (scores[i], -i))
                    r = run_backtest(
                        bars,
                        targets[chosen],
                        replace(config, start=str(test_start.date()), end=str(test_end.date())),
                        events,
                    )
                    fold_results[(*key, year)] = r
                    folds.append(
                        {
                            "version": version,
                            "frequency": frequency,
                            "strategy": strategy,
                            "year": year,
                            "train_start": str(train_start.date()),
                            "train_end": str(train_end.date()),
                            "params": json.dumps(candidates[chosen], sort_keys=True),
                            **performance(r),
                        }
                    )
                    export_result(
                        r, output / version / frequency / "walk_forward" / strategy / str(year)
                    )
                # Cost sensitivity uses the fixed strategy, not a re-optimized one.
                fixed = signal(bars, strategy, (fixed_parameters or {}).get(strategy))
                for participation in [0.01, 0.1, 1.0]:
                    r = run_backtest(
                        bars,
                        fixed,
                        replace(config, start=start, end=end, max_participation=participation),
                        events,
                    )
                    capacity.append(
                        {
                            "version": version,
                            "frequency": frequency,
                            "strategy": strategy,
                            "participation_limit": participation,
                            "capacity_rejections": int((r.orders.reason == "capacity_limit").sum()),
                            **performance(r),
                        }
                    )
                for commission_bp, slippage_bp in [
                    (0, 0),
                    (0.5, 0),
                    (0.5, 1),
                    (0.5, 2),
                    (0.5, 5),
                    (0.5, 10),
                    (1, 1),
                    (3, 1),
                ]:
                    r = run_backtest(
                        bars,
                        fixed,
                        replace(
                            config,
                            start=start,
                            end=end,
                            commission=commission_bp / 10000,
                            slippage=slippage_bp / 10000,
                        ),
                        events,
                    )
                    sensitivity.append(
                        {
                            "version": version,
                            "frequency": frequency,
                            "strategy": strategy,
                            "commission_bp": commission_bp,
                            "slippage_bp": slippage_bp,
                            **performance(r),
                        }
                    )
                print(f"Research: {version}/{frequency}/{strategy}", flush=True)
    tables = {
        "parameter_experiments": pd.DataFrame(experiments),
        "selected_oos": pd.DataFrame(selections),
        "walk_forward": pd.DataFrame(folds),
        "cost_sensitivity": pd.DataFrame(sensitivity),
        "capacity_sensitivity": pd.DataFrame(capacity),
    }
    for name, frame in tables.items():
        frame.to_csv(output / f"{name}.csv", index=False)
    return tables, oos_results, fold_results
