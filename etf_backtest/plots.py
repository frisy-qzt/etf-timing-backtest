"""Offline Plotly figures plus a portable static notebook overview."""

import json
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.offline import get_plotlyjs
from plotly.subplots import make_subplots

from .metrics import daily_equity, daily_returns, drawdown, period_returns
from .strategies import DEFAULTS, LABELS

COLORS = {
    "trend": "#2457a7",
    "breakout": "#bc8420",
    "momentum": "#547149",
    "mean_reversion": "#bb628a",
    "buy_hold": "#30363e",
    "cash": "#939ba5",
}
PLOT_CONFIG = {
    "responsive": True,
    "displaylogo": False,
    "modeBarButtonsToRemove": ["sendDataToCloud", "sendChartToCloud"],
}
VERSION_LABELS = {"full": "全历史 · 2016—2026", "since_2022": "2022—2026"}
FREQ_LABELS = {"daily": "日频 · 下一交易日开盘", "60min": "60分钟 · 下一交易小时VWAP"}


def style(fig, title, height=500):
    fig.update_layout(
        template="plotly_white",
        title={"text": title, "x": 0.025},
        height=height,
        font={
            "family": "Arial, PingFang SC, Microsoft YaHei, sans-serif",
            "size": 13,
            "color": "#25344a",
        },
        margin={"l": 65, "r": 28, "t": 85, "b": 55},
        paper_bgcolor="white",
        plot_bgcolor="white",
        legend={"orientation": "h", "y": 1.08, "x": 0},
        hovermode="x unified",
    )
    fig.update_xaxes(showgrid=False)
    fig.update_yaxes(gridcolor="#e9edf2", zerolinecolor="#adb8c5")
    return fig


def equity_figure(group):
    fig = make_subplots(
        rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.10, row_heights=[0.65, 0.35]
    )
    for name, r in group.items():
        eq = daily_equity(r) / r.config.initial_cash
        dd = drawdown(r)
        line = {
            "color": COLORS[name],
            "width": 2,
            "dash": "dash" if name in ["buy_hold", "cash"] else "solid",
        }
        fig.add_trace(go.Scatter(x=eq.index, y=eq, name=LABELS[name], line=line), row=1, col=1)
        fig.add_trace(
            go.Scatter(x=dd.index, y=dd, name=LABELS[name], line=line, showlegend=False),
            row=2,
            col=1,
        )
    fig.update_yaxes(title_text="净值 / 初始资金=1", row=1, col=1)
    fig.update_yaxes(title_text="回撤", tickformat=".0%", row=2, col=1)
    return style(fig, "净值与回撤 · 固定参数，扣除佣金和滑点", 650)


def exposure_figure(group):
    fig = go.Figure()
    for name in DEFAULTS:
        r = group[name]
        fig.add_trace(
            go.Scatter(
                x=r.ledger.index,
                y=r.ledger.exposure,
                name=LABELS[name],
                line={"color": COLORS[name], "shape": "hv", "width": 1.2},
            )
        )
    fig.update_yaxes(tickformat=".0%", title="持仓市值 / 总权益", range=[-0.02, 1.05])
    return style(fig, "实际仓位 · 含T+1约束", 360)


def monthly_figure(group):
    fig = make_subplots(
        rows=2,
        cols=2,
        subplot_titles=[LABELS[x] for x in DEFAULTS],
        vertical_spacing=0.16,
        horizontal_spacing=0.12,
    )
    for i, name in enumerate(DEFAULTS):
        s = period_returns(group[name])
        d = pd.DataFrame({"year": s.index.year, "month": s.index.month, "return": s.values})
        grid = d.pivot(index="year", columns="month", values="return").reindex(columns=range(1, 13))
        fig.add_trace(
            go.Heatmap(
                z=grid.to_numpy() * 100,
                x=list(range(1, 13)),
                y=grid.index,
                colorscale=[[0, "#2457a7"], [0.5, "#f8f7f3"], [1, "#bc8420"]],
                zmin=-15,
                zmax=15,
                text=np.where(grid.notna(), np.round(grid * 100, 1).astype(str), ""),
                texttemplate="%{text}",
                colorbar={"title": "月收益%", "len": 0.4} if i == 0 else None,
                showscale=(i == 0),
                hovertemplate="%{y}年%{x}月<br>收益 %{z:.2f}%<extra></extra>",
            ),
            row=i // 2 + 1,
            col=i % 2 + 1,
        )
    fig.update_xaxes(title="月份", dtick=1)
    fig.update_yaxes(dtick=1)
    return style(fig, "月度收益 · 空白表示区间之外", 700)


def annual_figure(group):
    fig = go.Figure()
    for name, r in group.items():
        s = period_returns(r, "Y")
        fig.add_trace(
            go.Bar(x=s.index.astype(str), y=s.values, name=LABELS[name], marker_color=COLORS[name])
        )
    fig.update_yaxes(tickformat=".0%", title="年度收益")
    fig.update_layout(barmode="group")
    return style(fig, "年度收益 · 首尾年份可能不完整")


def trade_figure(group):
    fig = make_subplots(
        rows=2, cols=2, subplot_titles=[LABELS[x] for x in DEFAULTS], vertical_spacing=0.15
    )
    for i, name in enumerate(DEFAULTS):
        r = group[name]
        cut = r.ledger.index[-1] - pd.Timedelta(days=365)
        line = r.ledger.loc[cut:]
        fig.add_trace(
            go.Scatter(
                x=line.index,
                y=line.mark,
                name="原价",
                line={"color": "#69788a", "width": 1.1},
                showlegend=False,
            ),
            row=i // 2 + 1,
            col=i % 2 + 1,
        )
        for side, symbol in [("buy", "triangle-up"), ("sell", "triangle-down")]:
            f = r.fills[(r.fills.timestamp >= cut) & (r.fills.side == side)]
            fig.add_trace(
                go.Scatter(
                    x=f.timestamp,
                    y=f.execution_price,
                    mode="markers",
                    name="买入" if side == "buy" else "卖出",
                    showlegend=(i == 0),
                    marker={
                        "symbol": symbol,
                        "size": 9,
                        "color": "#2457a7" if side == "buy" else "#bc8420",
                    },
                    customdata=f[["quantity", "commission"]],
                    hovertemplate="%{x}<br>成交价 %{y:.4f}<br>份额 %{customdata[0]}<br>佣金 %{customdata[1]:.2f}<extra></extra>",
                ),
                row=i // 2 + 1,
                col=i % 2 + 1,
            )
    fig.update_yaxes(title="元 / 份")
    return style(fig, "最近一年成交标记 · ▲买入 / ▼卖出", 670)


def distribution_figure(group):
    fig = go.Figure()
    for name in DEFAULTS:
        t = group[name].trades
        closed = t.loc[t.status == "closed", "return"]
        fig.add_trace(
            go.Histogram(
                x=closed, name=LABELS[name], opacity=0.55, nbinsx=25, marker_color=COLORS[name]
            )
        )
    fig.update_layout(barmode="overlay")
    fig.update_xaxes(title="已平仓交易净收益率", tickformat=".0%")
    fig.update_yaxes(title="交易笔数")
    return style(fig, "往返交易收益分布 · 含佣金、滑点和应得分红")


def study_figures(tables, oos, walk, version, frequency):
    figures = {}
    params = tables["parameter_experiments"]
    params = params[
        (params.version == version) & (params.frequency == frequency) & (params.stage == "train")
    ]
    fig = make_subplots(
        rows=2,
        cols=2,
        subplot_titles=[LABELS[x] for x in DEFAULTS],
        horizontal_spacing=0.15,
        vertical_spacing=0.18,
    )
    axes = {
        "trend": ("fast", "slow"),
        "breakout": ("exit", "entry"),
        "momentum": ("threshold", "lookback"),
        "mean_reversion": ("entry_z", "lookback"),
    }
    for i, name in enumerate(DEFAULTS):
        d = params[params.strategy == name].copy()
        parsed = pd.DataFrame([json.loads(x) for x in d.params], index=d.index)
        x, y = axes[name]
        d[x] = parsed[x]
        d[y] = parsed[y]
        grid = d.pivot(index=y, columns=x, values="sharpe")
        fig.add_trace(
            go.Heatmap(
                x=grid.columns,
                y=grid.index,
                z=grid.to_numpy(),
                colorscale="Blues",
                showscale=False,
                text=np.where(grid.notna(), np.round(grid, 2).astype(str), ""),
                texttemplate="%{text}",
                hovertemplate=f"{x}=%{{x}}<br>{y}=%{{y}}<br>Sharpe=%{{z:.3f}}<extra></extra>",
            ),
            row=i // 2 + 1,
            col=i % 2 + 1,
        )
        fig.update_xaxes(title=x, type="category", row=i // 2 + 1, col=i % 2 + 1)
        fig.update_yaxes(title=y, type="category", row=i // 2 + 1, col=i % 2 + 1)
    figures["parameters"] = style(fig, "参数稳定性 · 仅训练集Sharpe，不展示测试集全网格", 630)
    cost = tables["cost_sensitivity"]
    cost = cost[
        (cost.version == version) & (cost.frequency == frequency) & (cost.commission_bp == 0.5)
    ]
    fig = go.Figure()
    for name in DEFAULTS:
        d = cost[cost.strategy == name].sort_values("slippage_bp")
        fig.add_trace(
            go.Scatter(
                x=d.slippage_bp,
                y=d.total_return,
                mode="lines+markers",
                name=LABELS[name],
                line_color=COLORS[name],
            )
        )
    fig.update_xaxes(title="单边滑点（bp），佣金固定0.5bp")
    fig.update_yaxes(title="累计收益", tickformat=".0%")
    figures["costs"] = style(fig, "滑点敏感性 · 固定策略参数")
    cap = tables["capacity_sensitivity"]
    cap = cap[(cap.version == version) & (cap.frequency == frequency)]
    fig = go.Figure()
    for name in DEFAULTS:
        d = cap[cap.strategy == name]
        fig.add_trace(
            go.Bar(
                x=(d.participation_limit * 100).astype(str) + "%",
                y=d.total_return,
                name=LABELS[name],
                marker_color=COLORS[name],
            )
        )
    fig.update_xaxes(
        title="订单量 / 前一完整日成交量上限"
        if frequency == "daily"
        else "订单量 / 已观察窗口成交量上限"
    )
    fig.update_yaxes(title="累计收益", tickformat=".0%")
    figures["capacity"] = style(fig, "容量压力对照 · 超限整笔拒绝，非真实订单簿模拟")
    fig = go.Figure()
    for name in DEFAULTS:
        r = oos[(version, frequency, name)]
        s = daily_equity(r) / r.config.initial_cash
        fig.add_trace(go.Scatter(x=s.index, y=s, name=LABELS[name], line_color=COLORS[name]))
    fig.update_yaxes(title="测试集独立净值")
    figures["oos"] = style(fig, "独立测试集 · 训练筛选前三，验证选一，测试仅运行选定参数")
    fig = go.Figure()
    for name in DEFAULTS:
        selected = sorted(
            [(key[-1], r) for key, r in walk.items() if key[:3] == (version, frequency, name)]
        )
        if selected:
            ret = pd.concat([daily_returns(r) for _, r in selected])
            nav = (1 + ret).cumprod()
            fig.add_trace(
                go.Scatter(x=nav.index, y=nav, name=LABELS[name], line_color=COLORS[name])
            )
    fig.update_yaxes(title="折间收益连乘指数")
    figures["walk_forward"] = style(
        fig, "滚动样本外 · 三年训练/下一年测试，各折独立空仓，不代表连续实盘"
    )
    return figures


def group_figures(results, tables, oos, walk, version, frequency):
    group = {key[2]: r for key, r in results.items() if key[:2] == (version, frequency)}
    return {
        "equity": equity_figure(group),
        "exposure": exposure_figure(group),
        "monthly": monthly_figure(group),
        "annual": annual_figure(group),
        "trades": trade_figure(group),
        "distribution": distribution_figure(group),
        **study_figures(tables, oos, walk, version, frequency),
    }


def static_overview(results, path: Path):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axs = plt.subplots(2, 2, figsize=(14, 8), layout="constrained")
    for ax, (version, freq) in zip(
        axs.ravel(), [(v, f) for v in VERSION_LABELS for f in FREQ_LABELS]
    ):
        for name in [*DEFAULTS, "buy_hold"]:
            r = results[(version, freq, name)]
            s = daily_equity(r) / r.config.initial_cash
            ax.plot(
                s.index,
                s,
                color=COLORS[name],
                label=name,
                linewidth=1.4,
                linestyle="--" if name == "buy_hold" else "-",
            )
        ax.set_title(f"{version} / {freq}")
        ax.set_ylabel("NAV (initial = 1)")
        ax.grid(alpha=0.15)
    handles, labels = axs[0, 0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="outside lower center", ncol=5, frameon=False)
    fig.suptitle(
        "CSI 1000 ETF | Fixed parameters, T+1, commission 0.5 bp + slippage 1 bp", fontsize=14
    )
    fig.savefig(path, dpi=150)
    plt.close(fig)


def export_report(results, summary, tables, oos, walk, quality, output: Path):
    all_figures = {}
    groups = []
    pct_cols = [
        "total_return",
        "annual_return",
        "max_drawdown",
        "win_rate",
        "average_exposure",
        "max_participation",
    ]
    for version in VERSION_LABELS:
        for frequency in FREQ_LABELS:
            key = f"{version}_{frequency}"
            figures = group_figures(results, tables, oos, walk, version, frequency)
            all_figures[(version, frequency)] = figures
            table = summary[(summary.version == version) & (summary.frequency == frequency)].copy()
            max_part = table.max_participation.max()
            selected = [
                "strategy",
                "total_return",
                "annual_return",
                "max_drawdown",
                "sharpe",
                "closed_trades",
                "win_rate",
                "average_exposure",
                "max_participation",
            ]
            table = table[selected]
            table["strategy"] = table.strategy.map(LABELS)
            for col in pct_cols:
                if col in table:
                    table[col] = table[col].map(lambda x: f"{x:.2%}" if pd.notna(x) else "—")
            table = table.rename(
                columns={
                    "strategy": "策略",
                    "total_return": "累计收益",
                    "annual_return": "年化收益",
                    "max_drawdown": "最大回撤",
                    "sharpe": "Sharpe",
                    "closed_trades": "已平仓笔数",
                    "win_rate": "胜率",
                    "average_exposure": "平均仓位",
                    "max_participation": "最大成交参与率",
                }
            )
            note = (
                f"本组最大订单参与率 {max_part:.1%}。主结果为不限制容量的价格基准，容量压力对照另列；不可将超过市场成交量的订单视为可实现成交。"
                if max_part > 1
                else f"本组最大订单参与率 {max_part:.1%}。VWAP为执行价格代理，容量压力对照使用事后观察成交量作压力筛查。"
            )
            body = f'<h2>{VERSION_LABELS[version]} / {FREQ_LABELS[frequency]}</h2><p class="note">{note}</p><div class="table-wrap">{table.to_html(index=False, border=0, na_rep="—", float_format=lambda x: f"{x:.3f}")}</div>'
            for name, fig in figures.items():
                body += f'<section class="figure">{fig.to_html(full_html=False, include_plotlyjs=False, config=PLOT_CONFIG, div_id=key + "_" + name)}</section>'
            groups.append((key, body))
    style_css = """body{margin:0;background:#f4f6f9;color:#25344a;font:15px/1.7 Arial,"PingFang SC",sans-serif}main{max-width:1280px;margin:auto;padding:42px 28px}h1{font-size:36px;line-height:1.2;letter-spacing:-1px}h2{font-size:23px;margin-top:36px}.eyebrow{color:#547149;letter-spacing:3px;font-size:12px}.subtitle{color:#6c798b;max-width:900px}.metrics{display:grid;grid-template-columns:repeat(4,1fr);gap:18px;margin:28px 0}.metric{padding:18px 22px;background:white;border-top:3px solid #2457a7}.metric b{display:block;font-size:27px}.metric span{font-size:12px;color:#6c798b}nav{position:sticky;top:0;background:#f4f6f9ee;padding:12px 0;z-index:10;display:flex;gap:10px;flex-wrap:wrap}button{background:white;border:1px solid #ccd5e0;padding:11px 18px;cursor:pointer;color:#25344a;font-size:14px;border-radius:4px}button.active{background:#2457a7;color:white;border-color:#2457a7}.note{background:#fbf2de;padding:14px 18px;border-left:3px solid #bc8420}.figure{background:white;margin:24px 0;padding:8px;border:1px solid #e5eaf0}.table-wrap{overflow:auto;background:white}table{border-collapse:collapse;width:100%;font-size:13px;white-space:nowrap}th,td{padding:13px 14px;text-align:right;border-bottom:1px solid #e5eaf0}th:first-child,td:first-child{text-align:left}th{color:#617287;font-weight:500}.panel[hidden]{display:none}details{padding:18px;background:white;margin-top:24px}a{color:#2457a7}footer{color:#6c798b;font-size:12px;margin:32px 0}@media(max-width:700px){main{padding:22px 10px}h1{font-size:27px}.metrics{grid-template-columns:repeat(2,1fr)}.figure{padding:0}button{padding:9px 11px}}@media print{nav{position:static}.panel[hidden]{display:block}.figure{break-inside:avoid}}"""
    style_css += """nav{background:#f4f6f9}.figure{overflow:hidden}@media(max-width:700px){.figure{overflow-x:auto}.figure .plotly-graph-div{min-width:950px}.figure::before{content:"图表可横向滚动查看完整细节";display:block;padding:6px 12px;font-size:12px;color:#6c798b}.panel{min-width:0}}"""
    cfg = next(iter(results.values())).config
    header = f"""<div class="eyebrow">SYSTEMATIC RESEARCH / 512100.SH</div><h1>中证1000ETF · 择时研究</h1><p class="subtitle">双频率 × 双日期区间。固定参数、独立样本外、滚动验证与成本容量对照。仅多头或空仓，严格T+1；小时信号使用下一交易小时VWAP。</p><div class="metrics"><div class="metric"><b>{cfg.initial_cash / 10000:g}万元</b><span>各版本独立初始资金</span></div><div class="metric"><b>{cfg.commission * 10000:g} + {cfg.slippage * 10000:g} bp</b><span>单边佣金 + 滑点 · 最低佣金{cfg.minimum_commission:g}元</span></div><div class="metric"><b>4 × 4</b><span>四策略 × 四组回测</span></div><div class="metric"><b>{summary.end.max()}</b><span>数据截止日期</span></div></div>"""
    provenance = '<details><summary>数据、复权与研究边界</summary><p>日线成交额×1000、两频成交量×100。2023-06-05四根小时K线从241条原始分钟记录重建。3个小时VWAP越界窗口禁止成交。官方核验1次份额合并、2次分红；折算按账户总份额向上取整，分红除息日记应收、支付日转现金。</p><p>信号采用事件生效后才变化的前向复权；2022-09-02源因子补前值只用于停牌占位价的核验。2026-09-30源因子参考列设1，事件累计因子不重置。现金股利保留完整精度，公告未明确分位舍入。固定参数全样本比较用于研究，不能替代独立样本外结果。</p><p>参数筛选按每个版本交易日60/20/20分割。训练选前三、验证选一、测试仅评估已选参数。滚动折独立初始化；折间连乘是评估指数，非连续持仓策略。参数网格小而固定，未对测试集反复优化。</p><p><a href="../../data/research/EVENT_EVIDENCE.md">官方事件证据</a> · <a href="../../data/research/quality_report.json">数据质量报告</a> · <a href="summary.csv">完整指标CSV</a> · <a href="selected_oos.csv">样本外CSV</a></p></details>'
    buttons = "".join(
        f'<button data-panel="{key}" class="{"active" if i == 0 else ""}">{VERSION_LABELS[v]} / {f}</button>'
        for i, ((key, _), (v, f)) in enumerate(zip(groups, all_figures))
    )
    panels = "".join(
        f'<div class="panel" id="{key}" {"hidden" if i else ""}>{body}</div>'
        for i, (key, body) in enumerate(groups)
    )
    script = """document.querySelectorAll('button[data-panel]').forEach(b=>b.onclick=()=>{document.querySelectorAll('.panel').forEach(p=>p.hidden=p.id!==b.dataset.panel);document.querySelectorAll('button[data-panel]').forEach(x=>x.classList.toggle('active',x===b));document.querySelectorAll('#'+b.dataset.panel+' .js-plotly-plot').forEach(p=>Plotly.Plots.resize(p));});"""

    def page(content):
        return (
            '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>中证1000ETF双频回测研究</title><style>'
            + style_css
            + "</style><script>"
            + get_plotlyjs()
            + "</script></head><body><main>"
            + header
            + content
            + provenance
            + "<footer>本地可复现研究 · 数据截至2026-09-30 · 原始行情保留 · 不包含实盘交易连接</footer></main></body></html>"
        )

    (output / "report.html").write_text(
        page("<nav>" + buttons + "</nav>" + panels + "<script>" + script + "</script>")
    )
    for key, body in groups:
        (output / f"report_{key}.html").write_text(page(body))
    return all_figures
