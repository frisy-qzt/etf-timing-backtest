# 策略与因子 API

## 运行策略

在仓库根目录安装项目后运行：

```python
from etf_backtest import BacktestConfig, run_strategy
from examples.zz1000_daily_demo import load_sample

bars, events = load_sample()
run = run_strategy(
    factor_name="日频均线",
    strategy="trend",
    frequency="daily",
    bars=bars,
    events=events,
    params={"fast": 20, "slow": 60},
    config=BacktestConfig(start="2022-01-01", end="2026-09-30"),
    output_root="output/trend",
)
print(run.metrics)
print(run.report_path)
```

| 参数 | 说明 |
|---|---|
| `factor_name` | 报告名称；1–80 个中文、字母、数字、下划线或连字符 |
| `strategy` | 内置名称、callable 或完全对齐的目标 Series |
| `frequency` | `daily` 或 `1min`–`120min`；须与行情列一致 |
| `bars`, `events` | 标准行情及公司行动，见 [数据契约](data-contract.md) |
| `params` | 传给策略和因子函数的参数字典 |
| `config` | 资金、费用、区间和成交设置 |
| `factor` | 可选的连续因子 Series 或 callable |
| `factor_analysis` | 是否启用因子检验，默认 `True` |
| `output_root` | 报告目录，默认 `output/factors` |

内置策略为 `trend`、`breakout`、`momentum`、`mean_reversion`、`buy_hold`、`cash`。窗口参数以当前频率有效 K 线根数计数。每次调用独立从现金开始；设置评估区间时，之前的数据仅用于策略预热。

## 自定义策略

策略函数签名为 `strategy(bars, **params) -> pandas.Series`，返回与输入索引完全一致的序列：0 空仓、1 持有、NaN 不更新目标。

```python
import pandas as pd
from etf_backtest import BacktestConfig, run_strategy
from examples.zz1000_daily_demo import load_sample


def momentum_target(bars: pd.DataFrame, lookback: int = 60) -> pd.Series:
    close = bars.loc[bars.valid, "signal_close"]
    momentum = close / close.shift(lookback) - 1
    target = (momentum > 0).astype(float).where(momentum.notna(), 0.0)
    return target.reindex(bars.index)


bars, events = load_sample()
run = run_strategy(
    "自定义动量", momentum_target, bars=bars, events=events,
    frequency="daily", params={"lookback": 60},
    config=BacktestConfig(start="2022-01-01", end="2026-09-30"),
)
```

引擎处理成交延迟，策略不要再额外 shift 一根。不要使用负 shift、居中窗口或全样本标准化。检查因果性时，应验证截断数据重算结果与完整信号前缀一致：

```python
cut = len(bars) // 2
pd.testing.assert_series_equal(
    momentum_target(bars).iloc[:cut], momentum_target(bars.iloc[:cut])
)
```

`support_resistance.py` 提供支撑反弹、压力突破、突破回踩和假跌破策略函数，可作为 callable 使用，不是内置字符串名称。

## 成交与账户模型

- 日频信号在收盘产生，下一交易日开盘执行；不使用执行日的未来收盘价或总成交量决定普通开盘单。
- 分钟信号在窗口结束产生，下一窗口执行。`run_strategy` 默认 `intraday_price="extreme"`：买 high、卖 low；也可选择 `"vwap"` 或两侧均取 high 的 `"high"`。
- 低层 `run_backtest` 默认 VWAP。成交模型优先级为入口显式参数、config 参数、入口默认值。
- 每个半日 120 分钟，不跨午休；不能整除 120 的频率保留尾段。09:30 集合竞价归入首根窗口。
- 初始资金默认 100 万元；单边佣金 0.5bp、最低佣金 0 元、滑点 1bp，买入 100 份整手，执行 T+1。
- 分红按登记日权益确认，除息日形成应收、支付日转现金；折算按事件指定比例和取整规则处理。
- 期末按市值估值，不强平；无杠杆、现金利息或自动分红再投资。

停牌、缺失及涨跌停约束依赖传入数据。`max_participation` 超限时整笔拒单，不模拟部分成交。分钟使用执行窗口量作事后容量筛选，日频使用上一完整日成交量。所有成交价均为研究代理，不保证可实现交易。

`BacktestConfig.end` 默认 `2026-09-30`；接入新时段数据请显式传入日期。

## 因子检验

因子检验使用连续值，不把 0 / 1 仓位当作连续因子。内置策略自动提供：均线相对价差、回看收益、通道位置或标准分；`buy_hold` 与 `cash` 没有连续因子。自定义因子通过 `factor=` 传入 Series 或 `factor(bars, **params)`。

| 检验 | 定义 |
|---|---|
| IC / Rank IC | 与未来 h 根有效 K 线收益的 Pearson / Spearman 相关，按 h 根抽样避免收益重叠 |
| ICIR | 非重叠窗口 IC 的均值 / 标准差，另给全样本 IC 和年化值 |
| IC 衰减 | 默认 `horizons=[1, 2, 3, 5, 10, 20]` |
| 分组 | 使用截至当前样本的滚动分位阈值，默认 5 组；每组仅在对应状态持有 |

`ic_window` 默认为日频 60、分钟 240 个抽样点；`group_window` 默认 252。窗口过短或样本不足会影响统计解释。这是单标的时序分析，不是多标的截面 IC。分组多空仅为统计对照。

## 返回结果与报告

```python
run.report_path        # HTML 的绝对路径
run.metrics            # 绩效指标字典
run.factor             # FactorReport 或 None
run.result.ledger      # 逐周期账户账本
run.result.orders      # 订单及拒单原因
run.result.fills       # 成交记录
run.result.trades      # 往返交易
run.result.actions     # 公司行动
```

报告保存为 `{output_root}/{factor_name}report.html`，图表依赖内嵌。成功后原子替换同名文件；运行失败保留上一份结果。比较多个实验时使用不同名称或目录。

报告包含策略、买入持有及空仓基准、因子检验、成本参数、数据质量、文件指纹和环境版本。成交预览仅显示前 200 笔，完整明细应读取返回对象。
