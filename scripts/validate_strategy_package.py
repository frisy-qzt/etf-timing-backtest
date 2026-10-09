"""Real-source integration checks; reports are kept as development examples."""

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
if (ROOT / ".runtime_deps").exists():
    sys.path.insert(0, str(ROOT / ".runtime_deps"))
from etf_backtest import BacktestConfig, load_market_data, run_strategy

rows = []
for frequency in ["1min", "5min", "15min", "30min", "60min", "daily"]:
    start = time.perf_counter()
    bars, events = load_market_data(ROOT, frequency)
    run = run_strategy(
        "验收趋势_" + frequency,
        "trend",
        frequency=frequency,
        bars=bars,
        events=events,
        config=BacktestConfig(start="2022-01-01"),
        output_root=ROOT / "output/factors",
    )
    r = run.result
    assert (r.ledger.cash >= -1e-6).all()
    assert (r.fills.signal_time < r.fills.timestamp).all()
    assert r.ledger.shares.iloc[0] == 0
    if frequency != "daily":
        for side, field in [("buy", "high"), ("sell", "low")]:
            f = r.fills[r.fills.side == side]
            assert (f.reference_price.to_numpy() == bars.loc[f.timestamp, field].to_numpy()).all()
    rows.append(
        {
            "frequency": frequency,
            "source_bars": len(bars),
            "evaluated_bars": len(r.ledger),
            "fills": len(r.fills),
            "seconds": round(time.perf_counter() - start, 2),
            "report_path": str(run.report_path),
            "missing_bars": int(bars.missing_bar.sum()) if "missing_bar" in bars else 0,
        }
    )
    print(rows[-1], flush=True)
print("六频回测验证完成，每次调用仅输出一个中文HTML报告。")
