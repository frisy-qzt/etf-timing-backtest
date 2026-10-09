"""Run a daily ETF strategy using the bundled 512100.SH dataset."""

import argparse
import hashlib
import json
from pathlib import Path

import pandas as pd

from etf_backtest import BacktestConfig, StrategyRun, run_strategy
from etf_backtest.engine import validate_events

DATA_DIR = Path(__file__).resolve().parent / "data" / "zz1000"


def load_sample(data_dir: str | Path = DATA_DIR) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Load and verify the released snapshot; units are CNY and shares."""
    folder = Path(data_dir)
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    for name in ("512100.SH_daily.parquet", "corporate_actions.csv"):
        path = folder / name
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != manifest["files"][name]["sha256"]:
            raise ValueError(f"Sample checksum mismatch: {name}")
    bars = pd.read_parquet(folder / "512100.SH_daily.parquet")
    if not isinstance(bars.index, pd.DatetimeIndex):
        raise ValueError("Sample must use a DatetimeIndex")
    if bars.index.has_duplicates or not bars.index.is_monotonic_increasing:
        raise ValueError("Sample timestamps must be sorted and unique")
    if len(bars) != manifest["rows"] or not bars.code.eq("512100.SH").all():
        raise ValueError("Sample symbol or row count does not match manifest")
    if (str(bars.index.min().date()), str(bars.index.max().date())) != (
        manifest["start"],
        manifest["end"],
    ):
        raise ValueError("Sample date range does not match manifest")
    events = pd.read_csv(folder / "corporate_actions.csv").rename(
        columns={
            "event_id": "id",
            "event_type": "kind",
            "effective_date": "ex_date",
            "share_ratio": "ratio",
        }
    )
    events["kind"] = events.kind.replace({"consolidation": "split", "cash_dividend": "dividend"})
    events["rounding"] = events.rounding.replace({"ceil_total_account_shares": "ceil"})
    validate_events(events)
    return bars, events


def run_demo(
    output_root: str | Path = "output/zz1000",
    *,
    data_dir: str | Path = DATA_DIR,
    start: str | None = None,
    end: str | None = None,
    fast: int = 20,
    slow: int = 60,
) -> StrategyRun:
    """Run causal moving averages with next-open execution and event accounting."""
    bars, events = load_sample(data_dir)
    return run_strategy(
        "南方中证1000ETF_日频双均线",
        "trend",
        frequency="daily",
        bars=bars,
        events=events,
        output_root=output_root,
        params={"fast": fast, "slow": slow},
        config=BacktestConfig(
            start=start or str(bars.index.min().date()),
            end=end or str(bars.index.max().date()),
        ),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR)
    parser.add_argument("--output-root", type=Path, default=Path("output/zz1000"))
    parser.add_argument("--start", help="Evaluation start date, YYYY-MM-DD")
    parser.add_argument("--end", help="Evaluation end date, YYYY-MM-DD")
    parser.add_argument("--fast", type=int, default=20)
    parser.add_argument("--slow", type=int, default=60)
    args = parser.parse_args()
    run = run_demo(
        args.output_root,
        data_dir=args.data_dir,
        start=args.start,
        end=args.end,
        fast=args.fast,
        slow=args.slow,
    )
    print("南方中证1000ETF（512100.SH）日频研究演示；历史结果不代表未来收益。")
    print(f"回测行数：{len(run.result.ledger)}；成交笔数：{len(run.result.fills)}")
    print(f"中文离线报告：{run.report_path}")


if __name__ == "__main__":
    main()
