"""全中文离线报告；绘图抽样不改变绩效统计，磁盘只保留 HTML。"""

import os
import tempfile
from html import escape
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.colors import sample_colorscale
from plotly.subplots import make_subplots

from .factor import METHOD_LABELS as FACTOR_METHOD_LABELS
from .metrics import daily_equity, drawdown, performance, period_returns

METRIC_LABELS = {
    "start": "开始日期",
    "end": "结束日期",
    "trading_days": "交易日数",
    "total_return": "累计收益率",
    "annual_return": "年化收益率",
    "volatility": "年化波动率",
    "sharpe": "夏普比率",
    "sortino": "索提诺比率",
    "max_drawdown": "最大回撤",
    "calmar": "卡玛比率",
    "longest_underwater_sessions": "最长回撤持续交易日数",
    "trough_to_recovery_calendar_days": "回撤谷底至恢复的自然日数",
    "drawdown_unrecovered": "期末回撤尚未恢复",
    "closed_trades": "已完成往返交易数",
    "win_rate": "交易胜率",
    "payoff_ratio": "平均盈亏比",
    "profit_factor": "盈利因子",
    "average_holding_days": "平均持有自然日数",
    "annual_turnover": "年化换手倍数",
    "average_exposure": "平均市场暴露",
    "commission_yuan": "累计佣金（元）",
    "slippage_yuan": "累计滑点成本（元）",
    "explicit_cost_over_initial": "直接成本占初始资金比例",
    "max_participation": "最高成交量参与比例",
    "final_equity": "期末总资产（元）",
}
PERCENT_METRICS = {
    "total_return",
    "annual_return",
    "volatility",
    "max_drawdown",
    "win_rate",
    "average_exposure",
    "explicit_cost_over_initial",
    "max_participation",
}
REASON_LABELS = {
    "t_plus_one": "当日买入份额不可卖出",
    "target_changed": "目标仓位变更",
    "filled": "已成交",
    "missing_or_no_trade": "行情缺失、停牌或无成交",
    "invalid_vwap": "成交均价超出价格范围",
    "limit_up": "涨停限制",
    "limit_down": "跌停限制",
    "capacity_limit": "超过成交量参与限制",
    "insufficient_cash_or_lot": "现金不足或不足一手",
    "end_of_range": "区间结束尚未成交",
}
PRICE_LABELS = {
    "extreme": "下一窗口买入取最高价、卖出取最低价",
    "high": "下一窗口买卖均取最高价",
    "vwap": "下一窗口成交量加权均价",
    "next_open": "下一交易日开盘价",
}
STRATEGY_LABELS = {
    "trend": "均线趋势",
    "breakout": "通道突破",
    "momentum": "时间序列动量",
    "mean_reversion": "均值回归",
    "buy_hold": "买入持有",
    "cash": "始终空仓",
    "supplied_targets": "外部目标仓位序列",
}
CONFIG_LABELS = {
    "initial_cash": "初始资金（元）",
    "commission": "单边佣金比例",
    "minimum_commission": "最低佣金（元）",
    "slippage": "单边滑点比例",
    "lot_size": "买入整手份额",
    "start": "配置开始日期",
    "end": "配置结束日期",
    "max_participation": "成交量参与比例上限",
    "intraday_price": "分钟成交模型",
}
FACTOR_IC_COLUMNS = [
    ("horizon", "持有期"),
    ("method", "相关口径"),
    ("ic_mean", "IC均值"),
    ("ic_std", "IC标准差"),
    ("icir", "ICIR（IC均值/标准差）"),
    ("icir_annual", "年化ICIR"),
    ("t_stat", "t值"),
    ("positive_ratio", "IC大于0占比"),
    ("blocks", "独立窗口数"),
    ("block_bars", "每窗口K线数"),
    ("full_sample_ic", "全样本IC"),
]
FACTOR_GROUP_COLUMNS = [
    ("group", "分组"),
    ("samples", "样本数"),
    ("share", "样本占比"),
    ("conditional_return", "条件平均收益"),
    ("annual_return", "组合年化收益"),
    ("volatility", "组合年化波动"),
    ("sharpe", "组合夏普比率"),
    ("max_drawdown", "组合最大回撤"),
    ("win_rate", "胜率"),
    ("final_nav", "组合期末净值"),
]
FACTOR_PERCENT = {
    "share",
    "conditional_return",
    "annual_return",
    "volatility",
    "max_drawdown",
    "win_rate",
}
FACTOR_METHOD_COLORS = {"pearson": "#287bc1", "spearman": "#e76158"}


def format_value(value, percent=False):
    if value is None or (isinstance(value, (float, np.floating)) and not np.isfinite(value)):
        return "不适用"
    if isinstance(value, (bool, np.bool_)):
        return "是" if value else "否"
    if isinstance(value, (int, np.integer)):
        return f"{value:,}"
    if isinstance(value, (float, np.floating)):
        return f"{value:.2%}" if percent else f"{value:,.4f}".rstrip("0").rstrip(".")
    return str(value)


def _table(headers, rows):
    head = "".join(f"<th>{escape(str(x))}</th>" for x in headers)
    body = "".join(
        "<tr>" + "".join(f"<td>{escape(str(x))}</td>" for x in row) + "</tr>" for row in rows
    )
    return f'<div class="table-wrap"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


def _date_axis(fig):
    fig.update_xaxes(tickformat="%Y年%m月", hoverformat="%Y年%m月%d日 %H:%M")


def _factor_group_label(group, quantiles):
    """分组名称：最低组、最高组与多空组合。"""
    if isinstance(group, str):
        return "多空组合（最高组做多、最低组做空）"
    value = int(group)
    if value == 1:
        return "第1组（最低）"
    return f"第{value}组（最高）" if value == int(quantiles) else f"第{value}组"


def _factor_tables(rep):
    """因子检验说明与两张中文表格。"""
    horizons = "、".join(f"{int(h)}" for h in rep.horizons)
    intro = (
        f"<p>因子检验使用<strong>未二值化</strong>的原始因子值：{escape(str(rep.name))}。"
        f"设置：IC 按每 {rep.ic_window} 个非重叠样本为一个独立窗口计算，分位阈值滚动窗口 "
        f"{rep.group_window} 个样本，分为 {rep.quantiles} 组，持有期 {horizons} 根K线；"
        "检验区间与回测评估区间一致。</p>"
        "<p>口径：单标的时序检验。持有期 h 的 IC 是因子值与之后 h 根有效K线收益的相关系数，"
        "计算前按每 h 根抽样一次，使未来收益不重叠；再把非重叠样本切成等长独立窗口，"
        "每个窗口一个 IC。ICIR 为窗口 IC 的均值与标准差之比，t 值按 √窗口数 折算，"
        "年化 ICIR 按每年独立窗口数折算。全样本 IC 是把所有样本合并后一次计算的相关系数，"
        "与窗口 IC 均值口径不同，窗口较短时两者可能符号相反，应结合 IC 衰减与分组结果判断。"
        "分组阈值只使用当期及之前的样本，不引入未来信息。</p>"
        "<p>分组对照中，每组是“仅在因子落入该组时持有标的、其余时间空仓”的择时组合；"
        "多空组合为最高组做多、最低组做空，其余时间空仓，属于统计对照，本包不支持实盘做空。"
        "条件平均收益只统计该组样本内的未来收益，与择时组合收益口径不同。</p>"
    )
    if not len(rep.ic):
        return (
            f"<section><h2>因子 IC 与 ICIR</h2>{intro}"
            "<p>有效样本不足，无法计算 IC，请扩大回测区间或缩小 IC 滚动窗口。</p></section>",
            [],
        )
    rows = [
        [
            f"{int(row.horizon)} 根",
            FACTOR_METHOD_LABELS.get(row.method, row.method),
            format_value(row.ic_mean),
            format_value(row.ic_std),
            format_value(row.icir),
            format_value(row.icir_annual),
            format_value(row.t_stat),
            format_value(row.positive_ratio, True),
            format_value(row.blocks),
            format_value(row.block_bars),
            format_value(row.full_sample_ic),
        ]
        for row in rep.ic.itertuples()
    ]
    tables = (
        f"<section><h2>因子 IC 与 ICIR</h2>{intro}"
        f"{_table([label for _, label in FACTOR_IC_COLUMNS], rows)}</section>"
    )
    if not rep.available:
        tables += (
            "<section><h2>因子分组收益对照</h2>"
            "<p>分组样本不足，未生成分组与多空对照。</p></section>"
        )
        return tables, _factor_figures(rep)
    monotonicity = "不适用" if not np.isfinite(rep.monotonicity) else f"{rep.monotonicity:.3f}"
    group_rows = [
        [_factor_group_label(row.group, rep.quantiles)]
        + [
            format_value(getattr(row, key), key in FACTOR_PERCENT)
            for key, _ in FACTOR_GROUP_COLUMNS[1:]
        ]
        for row in rep.groups.itertuples()
    ]
    note = (
        f"<p>分组单调性（各组条件平均收益与组序号的秩相关）：<strong>{monotonicity}</strong>。"
        "越接近 1 表示因子值越高、未来收益越高；接近 −1 表示因子为反向指标。"
        f"调仓样本 {format_value(int(rep.groups.samples[:-1].sum()))} 个，持有期 {rep.holding} 根K线。</p>"
    )
    tables += (
        f"<section><h2>因子分组收益对照</h2>{note}"
        f"{_table([label for _, label in FACTOR_GROUP_COLUMNS], group_rows)}</section>"
    )
    return tables, _factor_figures(rep)


def _factor_figures(rep):
    """IC 衰减曲线与分组、多空组合净值图。"""
    hover = "%{x|%Y年%m月%d日 %H:%M}<br>%{fullData.name}：%{y:.4f}<extra></extra>"
    figures = []
    spread = rep.ic.pivot(index="horizon", columns="method", values="ic_std")
    decay = go.Figure()
    for method in rep.decay.columns:
        line = dict(
            x=[int(h) for h in rep.decay.index],
            y=rep.decay[method].to_numpy(dtype=float),
            name=FACTOR_METHOD_LABELS.get(method, method),
            mode="lines+markers",
            line_color=FACTOR_METHOD_COLORS.get(method),
            marker_size=8,
            hovertemplate=f"持有期 %{{x}} 根<br>{FACTOR_METHOD_LABELS.get(method, method)} IC均值：%{{y:.4f}}<extra></extra>",
        )
        if method in spread:
            line["error_y"] = dict(
                type="data", array=spread[method].to_numpy(dtype=float), visible=True, thickness=1.2
            )
        decay.add_scatter(**line)
    decay.add_hline(y=0, line_dash="dot", line_color="#8c9aa8")
    decay.update_xaxes(
        title_text="未来收益持有期（根K线）",
        tickmode="array",
        tickvals=[int(h) for h in rep.decay.index],
        ticktext=[f"{int(h)} 根" for h in rep.decay.index],
    )
    decay.update_yaxes(title_text="IC 均值（误差棒为 IC 标准差）")
    figures.append(("IC 衰减曲线（不同持有期）", decay, 470))
    if not rep.available:
        return figures
    names = [column for column in rep.nav.columns if column != "多空组合"]
    fig = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        row_heights=[0.66, 0.34],
        vertical_spacing=0.12,
        subplot_titles=(
            "分组择时组合净值（仅在该组持有）",
            "多空组合净值（最高组做多、最低组做空）",
        ),
    )
    palette = sample_colorscale("RdYlGn", [i / max(len(names) - 1, 1) for i in range(len(names))])
    for label, color in zip(names, palette):
        fig.add_trace(
            go.Scatter(
                x=rep.nav.index, y=rep.nav[label], name=label, line_color=color, hovertemplate=hover
            ),
            row=1,
            col=1,
        )
    fig.add_trace(
        go.Scatter(
            x=rep.nav.index,
            y=rep.nav["多空组合"],
            name="多空组合",
            line_color="#182d42",
            hovertemplate=hover,
        ),
        row=2,
        col=1,
    )
    fig.add_hline(y=1, line_dash="dot", line_color="#8c9aa8", row=2, col=1)
    _date_axis(fig)
    fig.update_yaxes(title_text="净值（起始为1）", row=1, col=1)
    fig.update_yaxes(title_text="多空净值", row=2, col=1)
    figures.append(("分组与多空组合净值", fig, 620))
    return figures


def export_strategy_report(
    result,
    benchmark,
    bars,
    path,
    name,
    frequency,
    *,
    cash=None,
    metadata=None,
    diagnostics=None,
    factor=None,
):
    """完整生成后原子替换目标；任何失败都不留下辅助文件或破坏旧报告。"""
    path = Path(path)
    metadata, diagnostics = metadata or {}, diagnostics or {}
    figures = []
    time_hover = "%{x|%Y年%m月%d日 %H:%M}<br>%{fullData.name}：%{y:.4f}<extra></extra>"
    rate_hover = "%{x|%Y年%m月%d日}<br>%{fullData.name}：%{y:.2%}<extra></extra>"
    fig = make_subplots(
        rows=2, cols=1, shared_xaxes=True, subplot_titles=("策略与基准净值", "日终回撤")
    )
    for label, r, color in [("因子策略", result, "#287bc1"), ("买入持有", benchmark, "#149d87")]:
        eq = daily_equity(r) / r.config.initial_cash
        fig.add_trace(
            go.Scatter(x=eq.index, y=eq, name=label, line_color=color, hovertemplate=time_hover),
            row=1,
            col=1,
        )
        dd = drawdown(r)
        fig.add_trace(
            go.Scatter(
                x=dd.index, y=dd, name=label + "回撤", line_color=color, hovertemplate=rate_hover
            ),
            row=2,
            col=1,
        )
    _date_axis(fig)
    fig.update_yaxes(title_text="净值（初始为1）", row=1, col=1)
    fig.update_yaxes(title_text="回撤比例", tickformat=".0%", row=2, col=1)
    figures.append(("净值与回撤（日终口径）", fig))
    led = result.ledger
    # 仅价格背景抽样；仓位保留切换点和前一点，所有成交点均保留。
    step = max(1, int(np.ceil(len(led) / 12000)))
    sampled = led.iloc[sorted(set(range(0, len(led), step)) | {len(led) - 1})]
    changes = (
        led[["shares", "target", "sellable"]]
        .ne(led[["shares", "target", "sellable"]].shift())
        .any(axis=1)
        .to_numpy()
    )
    change_rows = np.flatnonzero(changes)
    state_rows = sorted(
        set(range(0, len(led), step))
        | set(change_rows)
        | set(np.maximum(change_rows - 1, 0))
        | {len(led) - 1}
    )
    states = led.iloc[state_rows]
    fig = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        subplot_titles=("原始价格与成交位置", "实际仓位与目标仓位"),
    )
    fig.add_trace(
        go.Scatter(x=sampled.index, y=sampled.mark, name="原始估值价格", hovertemplate=time_hover),
        row=1,
        col=1,
    )
    for side, label, color, symbol in [
        ("buy", "买入", "#149d87", "triangle-up"),
        ("sell", "卖出", "#e76158", "triangle-down"),
    ]:
        f = result.fills[result.fills.side == side]
        fig.add_trace(
            go.Scatter(
                x=f.timestamp,
                y=f.execution_price,
                mode="markers",
                name=label,
                marker=dict(color=color, symbol=symbol, size=7),
                hovertemplate=time_hover,
            ),
            row=1,
            col=1,
        )
    for col, label in [("exposure", "实际仓位"), ("target", "目标仓位")]:
        fig.add_trace(
            go.Scatter(
                x=states.index, y=states[col], name=label, line_shape="hv", hovertemplate=rate_hover
            ),
            row=2,
            col=1,
        )
    _date_axis(fig)
    fig.update_yaxes(title_text="价格（元／份）", row=1, col=1)
    fig.update_yaxes(title_text="仓位比例", tickformat=".0%", row=2, col=1)
    figures.append(("成交标记、仓位与信号", fig))
    monthly = period_returns(result, "M")
    table = (
        pd.DataFrame(
            {"year": monthly.index.year, "month": monthly.index.month, "value": monthly.values}
        )
        .pivot(index="year", columns="month", values="value")
        .reindex(columns=range(1, 13))
    )
    fig = go.Figure(
        go.Heatmap(
            z=table.to_numpy(),
            x=[f"{x}月" for x in range(1, 13)],
            y=[f"{x}年" for x in table.index],
            colorscale="RdYlGn",
            zmid=0,
            texttemplate="%{z:.1%}",
            colorbar=dict(title="收益率", tickformat=".0%"),
            hovertemplate="%{y}%{x}<br>收益率：%{z:.2%}<extra></extra>",
        )
    )
    fig.update_xaxes(title_text="月份")
    fig.update_yaxes(title_text="年份")
    figures.append(("月度收益热图", fig))
    fig = go.Figure()
    for label, r in [("因子策略", result), ("买入持有", benchmark)]:
        annual = period_returns(r, "Y")
        fig.add_bar(
            x=[f"{x}年" for x in annual.index.year],
            y=annual.values,
            name=label,
            hovertemplate="%{x}<br>%{fullData.name}收益率：%{y:.2%}<extra></extra>",
        )
    fig.update_xaxes(title_text="年份")
    fig.update_yaxes(title_text="年度收益率", tickformat=".0%")
    figures.append(("年度收益比较", fig))
    fig = go.Figure()
    closed = result.trades[result.trades.status == "closed"]
    fig.add_histogram(
        x=closed["return"],
        name="已完成交易收益率",
        nbinsx=40,
        hovertemplate="交易收益率：%{x:.2%}<br>交易笔数：%{y}<extra></extra>",
    )
    if closed.empty:
        fig.add_annotation(text="暂无已完成的往返交易", showarrow=False)
    fig.update_xaxes(title_text="交易收益率", tickformat=".0%")
    fig.update_yaxes(title_text="交易笔数")
    figures.append(("已完成交易收益分布", fig))
    fig = go.Figure()
    for col, label in [("cash", "可用现金"), ("receivable", "应收分红")]:
        fig.add_scatter(x=sampled.index, y=sampled[col], name=label, hovertemplate=time_hover)
    fig.add_scatter(
        x=sampled.index, y=sampled.shares * sampled.mark, name="持仓市值", hovertemplate=time_hover
    )
    _date_axis(fig)
    fig.update_yaxes(title_text="金额（元）", tickformat=",.0f")
    figures.append(("现金、持仓市值与应收分红", fig))
    fig = go.Figure()
    fills = result.fills
    for col, label in [("commission", "累计佣金"), ("slippage_cost", "累计滑点成本")]:
        fig.add_scatter(
            x=fills.timestamp, y=fills[col].cumsum(), name=label, hovertemplate=time_hover
        )
    _date_axis(fig)
    fig.update_yaxes(title_text="累计成本（元）", tickformat=",.0f")
    figures.append(("累计交易成本", fig))
    reasons = result.orders.reason.value_counts()
    reason_names = [REASON_LABELS.get(x, "其他订单原因") for x in reasons.index]
    fig = go.Figure(
        go.Bar(x=reason_names, y=reasons.values, hovertemplate="%{x}<br>次数：%{y}<extra></extra>")
    )
    fig.update_yaxes(title_text="记录次数")
    figures.append(("订单执行与拒绝原因", fig))
    factor_tables, factor_figures = _factor_tables(factor) if factor is not None else ("", [])
    ordered = list(factor_figures) + [
        (title, fig, 600 if i < 2 else 410) for i, (title, fig) in enumerate(figures)
    ]
    sections = []
    for i, (title, fig, height) in enumerate(ordered):
        fig.update_layout(
            template="plotly_white",
            height=height,
            margin=dict(l=65, r=25, t=45, b=70),
            hovermode="closest",
            separators=".,",
        )
        # 隐藏 Plotly 英文工具栏，缩放、图例开关与悬浮提示仍可交互。
        content = fig.to_html(
            full_html=False,
            include_plotlyjs=True if i == 0 else False,
            config={"responsive": True, "displayModeBar": False, "displaylogo": False},
        )
        sections.append(f"<section><h2>{escape(title)}</h2>{content}</section>")
    metrics = performance(result)
    metric_rows = [
        (METRIC_LABELS[k], format_value(v, k in PERCENT_METRICS)) for k, v in metrics.items()
    ]
    comparison_rows = []
    for label, r in [("因子策略", result), ("买入持有", benchmark), ("始终空仓", cash)]:
        if r is None:
            continue
        m = performance(r)
        comparison_rows.append(
            [
                label,
                format_value(m["total_return"], True),
                format_value(m["annual_return"], True),
                format_value(m["max_drawdown"], True),
                format_value(m["sharpe"]),
                format_value(m["final_equity"]),
            ]
        )
    comparison = _table(
        ["策略", "累计收益率", "年化收益率", "最大回撤", "夏普比率", "期末资产（元）"],
        comparison_rows,
    )
    model = (
        diagnostics.get("price_model", result.config.intraday_price or "vwap")
        if frequency != "daily"
        else "next_open"
    )
    freq_label = "日频" if frequency == "daily" else frequency[:-3] + "分钟"
    config_rows = []
    for key, value in (metadata.get("config") or vars(result.config)).items():
        if key == "intraday_price":
            value = PRICE_LABELS.get(value, "成交量加权均价")
        elif key in ("commission", "slippage"):
            value = f"{value * 10000:.2f} 个基点（{value:.4%}）"
        elif key == "start" and value is None:
            value = "从行情首日开始"
        elif key == "max_participation" and value is None:
            value = "未设置容量上限"
        else:
            value = format_value(value, key in {"commission", "slippage", "max_participation"})
        config_rows.append((CONFIG_LABELS[key], value))
    params = metadata.get("params", {})
    param_labels = {
        "fast": "短均线根数",
        "slow": "长均线根数",
        "entry": "入场窗口根数",
        "exit": "退出窗口根数",
        "lookback": "回看根数",
        "threshold": "动量阈值",
        "entry_z": "入场标准分阈值",
        "exit_z": "退出标准分阈值",
    }
    param_rows = [(param_labels.get(k, k), format_value(v)) for k, v in params.items()]
    audit_rows = [
        ("报告生成时间（北京时间）", metadata.get("generated_at", "未记录")),
        (
            "策略",
            STRATEGY_LABELS.get(metadata.get("strategy"), metadata.get("strategy", "自定义策略")),
        ),
        ("包含预热的行情根数", format_value(diagnostics.get("rows", len(bars)))),
        ("评估区间账本根数", format_value(len(led))),
        ("无效行情根数（含预热）", format_value(diagnostics.get("invalid_bars"))),
        ("缺失窗口根数（含预热）", format_value(diagnostics.get("missing_bars"))),
        ("评估区间涨跌停价覆盖率", format_value(diagnostics.get("limit_price_coverage"), True)),
        ("已提供公司行动表", format_value(diagnostics.get("events_supplied"))),
        ("解释器版本", metadata.get("python", "未记录")),
    ]
    for key, label in [
        ("data_sha256", "行情数据指纹"),
        ("signal_sha256", "目标信号指纹"),
        ("events_sha256", "公司行动指纹"),
        ("strategy_sha256", "策略源码指纹"),
    ]:
        audit_rows.append((label, metadata.get(key) or "未提供或不可获取"))
    dependencies = "；".join(f"{k}：{v}" for k, v in metadata.get("dependencies", {}).items())
    if dependencies:
        audit_rows.append(("依赖版本", dependencies))
    if factor is not None:
        audit_rows.append(
            ("因子检验因子", metadata.get("factor_source", "内置策略因子（未二值化）"))
        )
        audit_rows.append(
            (
                "因子检验设置",
                f"IC 滚动窗口 {factor.ic_window} 个样本；分位阈值滚动窗口 "
                f"{factor.group_window} 个样本；{factor.quantiles} 组；持有期 "
                + "、".join(f"{int(h)}" for h in factor.horizons)
                + " 根K线",
            )
        )
        audit_rows.append(("因子值指纹", metadata.get("factor_sha256") or "未记录"))
        audit_rows.append(("分组对照样本", "已生成" if factor.available else "样本不足，未生成"))
    fill_rows = []
    for r in fills.head(200).itertuples():
        fill_rows.append(
            [
                r.timestamp.strftime("%Y-%m-%d %H:%M"),
                r.signal_time.strftime("%Y-%m-%d %H:%M"),
                "买入" if r.side == "buy" else "卖出",
                format_value(r.quantity),
                format_value(r.reference_price),
                format_value(r.execution_price),
                format_value(r.commission),
                format_value(r.slippage_cost),
            ]
        )
    fill_table = _table(
        [
            "成交时刻",
            "信号时刻",
            "方向",
            "份额",
            "参考价（元）",
            "成交价（元）",
            "佣金（元）",
            "滑点成本（元）",
        ],
        fill_rows,
    )
    cards = "".join(
        f'<div class="card"><span>{METRIC_LABELS[k]}</span><strong>{format_value(metrics[k], k in PERCENT_METRICS)}</strong></div>'
        for k in ("total_return", "annual_return", "max_drawdown", "sharpe", "final_equity")
    )
    if factor is not None and len(factor.ic):
        shortest = factor.ic[factor.ic.horizon == min(factor.horizons)]
        rank = shortest[shortest.method == "spearman"]
        if len(rank):
            horizon = int(rank.horizon.iloc[0])
            cards += (
                f'<div class="card"><span>Rank IC 均值（{horizon} 根）</span>'
                f"<strong>{format_value(rank.ic_mean.iloc[0])}</strong></div>"
                f'<div class="card"><span>ICIR（{horizon} 根）</span>'
                f"<strong>{format_value(rank.icir.iloc[0])}</strong></div>"
            )
    text = f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{escape(name)} 回测报告</title><style>
body{{font:16px system-ui;background:#edf2f6;color:#182d42;margin:0;padding:24px}}main{{max-width:1250px;margin:auto}}
section{{background:white;border-radius:12px;padding:20px;margin:20px 0;overflow:auto}}table{{border-collapse:collapse;width:100%}}
th,td{{padding:9px 14px;text-align:left;border-bottom:1px solid #eee;overflow-wrap:anywhere}}th{{background:#f5f8fb}}
h1{{font-size:30px}}h2{{font-size:21px}}p{{line-height:1.7}}.table-wrap{{overflow:auto}}.cards{{display:flex;flex-wrap:wrap;gap:12px}}
.card{{background:#fff;border-radius:10px;padding:18px;flex:1;min-width:150px}}.card span{{display:block;color:#60758a;font-size:14px}}
.card strong{{display:block;font-size:25px;margin-top:8px}}summary{{cursor:pointer;font-weight:600}}@media(max-width:600px){{body{{padding:12px}}section{{padding:12px}}h1{{font-size:24px}}}}
</style></head><body><main><h1>{escape(name)} · {freq_label}回测报告</h1>
<p>{escape(metrics["start"])} 至 {escape(metrics["end"])} · 成交模型：{PRICE_LABELS[model]}。信号后的下一个交易窗口执行，滑点另计。风险指标统一使用日终净值。</p>
<div class="cards">{cards}</div><section><h2>策略与基准对照</h2>{comparison}</section>
<section><h2>完整回测指标</h2>{_table(["指标", "数值"], metric_rows)}</section>{factor_tables}{"".join(sections)}
<section><h2>成交明细预览</h2><p>共 {len(fills):,} 笔成交，报告展示前 200 笔。完整账本、订单、成交、往返交易和公司行动保留在函数返回的回测结果中。</p>{fill_table}</section>
<section><h2>回测配置与策略参数</h2>{_table(["配置项", "取值"], config_rows)}{_table(["策略参数", "取值"], param_rows) if param_rows else "<p>使用内置默认参数或未指定参数。</p>"}</section>
<section><details><summary>数据诊断与复现信息</summary>{_table(["检查项", "结果"], audit_rows)}</details>
<p>价格背景曲线最多约 12,000 点；仓位图额外保留状态变化及相邻点，全部成交标记均保留。交易指标不抽样。信号使用事件复权价格，价格图显示原价。</p>
<p>股票型基金执行次交易日可卖规则；每次回测独立空仓启动，预热不计收益，期末不强制平仓。分钟尾段可短于设定时长。未提供完整涨跌停价时，相应限制无法完整模拟；成交量参与上限未设置时，不拒绝大额订单。</p>
<p>极值成交是事后压力模拟，无法保证实盘成交。自定义策略需另行核验因果性。收益按自然日年化，风险按每年252个交易日计算；未定义指标显示“不适用”。直接成本占资金比例不等于无成本策略的反事实收益差。</p>
<p>因子检验为单标的时序口径，IC 使用未二值化因子值，分组阈值只使用历史样本；IC 的独立窗口之间不重叠，t 值按 √窗口数 折算。窗口越短，窗口 IC 与全样本 IC 差异可能越大。多空组合为统计对照，本包不支持实盘做空。</p></section>
</main></body></html>"""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=".回测报告_",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write(text)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return path
