"""Verify saved notebook, complete experiment matrix, ledgers and offline HTML."""

import ast
import json
from html.parser import HTMLParser
from pathlib import Path

import nbformat
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output/backtest"
nb = nbformat.read(ROOT / "notebooks/中证1000ETF_双频双区间回测.ipynb", as_version=4)
nbformat.validate(nb)
code_cells = [c for c in nb.cells if c.cell_type == "code"]
for c in code_cells:
    ast.parse(c.source)
    assert c.execution_count is not None, "Unexecuted code cell"
    assert not any(o.output_type == "error" for o in c.outputs), "Notebook contains an error"

summary = pd.read_csv(OUT / "summary.csv")
assert len(summary) == 24 and not summary[["version", "frequency", "strategy"]].duplicated().any()
assert len(pd.read_csv(OUT / "selected_oos.csv")) == 16
assert len(pd.read_csv(OUT / "walk_forward.csv")) == 72
fill_count = 0
for row in summary.itertuples():
    p = OUT / row.version / row.frequency / row.strategy
    ledger = pd.read_parquet(p / "ledger.parquet")
    fills = pd.read_parquet(p / "fills.parquet")
    cfg = json.loads((p / "config.json").read_text())
    assert np.allclose(
        ledger.equity, ledger.cash + (ledger.shares * ledger.mark).fillna(0) + ledger.receivable
    )
    assert (ledger.cash >= -1e-6).all() and (ledger.sellable >= 0).all()
    assert np.isclose(ledger.equity.iloc[0], cfg["initial_cash"])
    assert np.isclose(ledger.equity.iloc[-1], row.final_equity)
    assert ledger.index.min().normalize() >= pd.Timestamp(cfg["start"])
    assert ledger.index.max().normalize() <= pd.Timestamp(cfg["end"])
    if len(fills):
        assert (fills.timestamp > fills.signal_time).all()
        assert not (fills.timestamp.dt.normalize() == pd.Timestamp("2022-09-02")).any()
        sign = np.where(fills.side == "buy", 1.0, -1.0)
        assert np.allclose(
            fills.execution_price, fills.reference_price * (1 + sign * cfg["slippage"])
        )
        assert np.allclose(
            fills.commission,
            np.maximum(cfg["minimum_commission"], fills.notional * cfg["commission"]),
        )
    trades = pd.read_parquet(p / "trades.parquet")
    closed = trades[trades.status == "closed"]
    if len(closed):
        assert (closed.exit_time.dt.normalize() > closed.entry_time.dt.normalize()).all(), (
            "T+1 breach"
        )
    fill_count += len(fills)


class ResourceAudit(HTMLParser):
    def __init__(self):
        super().__init__()
        self.external = []
        self.plots = 0

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in ("script", "link", "img"):
            for key in ("src", "href"):
                if a.get(key, "").startswith(("http://", "https://", "//")):
                    self.external.append(a[key])
        if "plotly-graph-div" in a.get("class", ""):
            self.plots += 1


html_counts = {}
for path in [OUT / "report.html", OUT / "notebook.html", *sorted(OUT.glob("report_*.html"))]:
    parser = ResourceAudit()
    parser.feed(path.read_text())
    assert not parser.external, f"External runtime resources in {path.name}: {parser.external}"
    html_counts[path.name] = parser.plots
assert html_counts["report.html"] == 44
assert all(v == 11 for k, v in html_counts.items() if k.startswith("report_"))
assert html_counts["notebook.html"] >= 13
report = {
    "notebook_code_cells_executed": len(code_cells),
    "fixed_runs": len(summary),
    "selected_oos_runs": 16,
    "walk_forward_folds": 72,
    "fills_audited": fill_count,
    "accounting_and_timing_checks": "passed",
    "external_runtime_resources": 0,
    "html_chart_counts": html_counts,
}
(OUT / "delivery_validation.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
print(json.dumps(report, ensure_ascii=False, indent=2))
