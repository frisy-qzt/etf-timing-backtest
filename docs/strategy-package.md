# ETF 择时回测包：新策略开发与测试

`etf-timing-backtest` 是可安装的 Python 包，导入名为 `etf_backtest`。支持仅多头／空仓；默认标的为南方中证1000ETF（512100.SH）。不联网、不下载数据、不发出实盘订单。旧双频 Notebook 仍可使用原来的 `run_backtest` 和 VWAP 模型。

## 安装

在项目根目录执行（Python 3.10+）：

```bash
python -m pip install -e .
python -m unittest discover -s tests -v
python examples/synthetic_demo.py
```

建议使用独立虚拟环境；无需本机专用解释器或离线依赖目录。wheel 只含代码；Git 仓库及源码包附带 `examples/data/zz1000/` 日频样本，不包含数 GB 数据湖、研究结果或 Notebook。离开项目目录后也可导入；`data_root` 指向保留原始行情的项目目录。

## 最短调用

```python
from etf_backtest import BacktestConfig, run_strategy

run = run_strategy(
    factor_name="均线趋势_5min",
    strategy="trend",
    frequency="5min",
    data_root="/path/to/local-data",
    output_root="output/factors",
    params={"fast": 20, "slow": 60},
    config=BacktestConfig(start="2022-01-01", end="2026-09-30"),
)
print(run.metrics)
print(run.report_path)
result = run.result  # ledger / orders / fills / trades / actions
```

内置策略：`trend`、`breakout`、`momentum`、`mean_reversion`、`buy_hold`、`cash`。参数单位均为当前频率有效 K 线根数，不是交易日。每次调用独立以现金启动，不继承另一运行的状态。

## 频率与成交时间轴

- 支持 `1min` 到 `120min` 的任意整数分钟；`daily`（别名 `1d`、`D`）为日频。没有横跨午休的 K 线。
- 每个半日长 120 分钟。不能整除 120 的频率（如 7min、90min）在 11:30、15:00 留一个较短尾段。因而当前频率并不保证每根时长完全相等。
- 原始 09:30 集合竞价归入首根窗口：1min 首根 09:31 包含 09:30 和 09:31 两条源记录；以后右端标记，不重复边界。
- 本根结束后生成目标，下一个交易窗口执行。默认分钟**买入参考价为下一区间 high，卖出参考价为下一区间 low**，并在该窗口结束时结算；再施加买入向上、卖出向下各 1bp 滑点。因此最终成交价可能超过原始高低区间。
- 60min：11:30 信号在 13:00–14:00 执行；15:00 信号在次交易日 09:30–10:30 执行。1min：11:30 信号对应 13:00–13:01。
- 日频：本日 15:00 信号，下交易日 09:30 按 open 执行，不读取当天 high/low/总成交量来决定普通开盘单。
- 最高／最低价是在执行窗口结束后才知道的模拟价格，不是能保证成交的报价；不允许用该窗口价格生成更早的信号。
- 新入口 `run_strategy(..., intraday_price="extreme")` 默认买 high／卖 low；研究比较可显式用 `"vwap"`（amount/volume），或 `"high"`（两侧均取 high）。未指定时低层 `run_backtest` 保持 VWAP，确保旧 Notebook 兼容；新入口默认 extreme。优先级为：调用时显式 intraday_price > config 中显式 intraday_price > 入口默认值。

## 策略函数协议

```python
import pandas as pd
from etf_backtest import run_strategy

def my_factor(bars: pd.DataFrame, lookback: int = 60) -> pd.Series:
    close = bars.loc[bars.valid, "signal_close"]
    momentum = close / close.shift(lookback) - 1
    # 因子值必须明确映射为目标仓位，不能把连续数值直接当仓位。
    targets = (momentum > 0).astype(float).where(momentum.notna(), 0.)
    return targets.reindex(bars.index)  # 缺失K线保留 NaN

run = run_strategy("自定义动量", my_factor, frequency="15min",
                   data_root="/path/to/data", params={"lookback": 60})
```

返回与输入索引完全对齐的 `pd.Series`：0 空仓、1 多头、NaN 不更新目标。不要自行 shift 延迟一根，引擎已处理延迟成交。也可直接传对齐 Series。策略看到区间前的历史用于预热，但只有评估区间内信号能创建订单，区间末信号不越界成交。策略不得使用负 shift、居中 rolling、全样本标准化或未来数据；引擎不能自动识别任意用户函数中的未来函数。

建议在新策略测试中比较完整序列信号的前缀与截断数据重算结果：

```python
full = my_factor(bars)
cut = len(bars) // 2
pd.testing.assert_series_equal(full.iloc[:cut], my_factor(bars.iloc[:cut]))
```

若调用多次，先 `bars, events = load_market_data(data_root, "5min")`，再用 `run_strategy(..., bars=bars, events=events)` 复用已读数据，避免重复扫描原始 Parquet。

## 自带数据与本地读取

本地入口读取 `daily/ETF_daily.parquet`、`1min/ETF_1min.parquet` 与 `data/research/corporate_actions.csv`。通过 Arrow 的 code 谓词只选 512100.SH；所有分钟频率从统一 1min 源重采样，避免不同频率源的量额差异改变实验。原始文件不修改。

本地源：日线 amount 千元×1000→元，分钟 amount 已为元；volume 手×100→份。外部传入 bars 时**已经必须是元／份，不会再次换算**。分钟聚合要求源分钟齐全，缺一条即把整个对应窗口标为 missing_bar/valid=False，不填造价格用于成交。停牌零量事实保留。收盘估值可使用同时刻的日线估值，盘中缺失维持上一可用估值。

外部 bars 要求唯一递增 DatetimeIndex；列 `open/high/low/close/amount/volume/frequency/valid`，信号列 `signal_open/high/low/close`。可提供 `mark_close`、`suspended`、`limit_up`、`limit_down`。时间为上海交易所本地、无时区标签，分钟按窗口结束标记，日频为日期。外部数据要自行提供完整交易日与窗口网格；引擎不会替外部数据推断缺失交易日。事件可传空表表示经调用者核验不存在事件；未传事件时报告会标记。

官方事件来源保留在原本地工作区 `data/research/EVENT_EVIDENCE.md`（不随代码分发）。研究信号使用因果前向事件因子：份额折算和已发生现金分红才改变因子；账户按原价、现金、份额、应收分红核算。未核验事件会报错。源 qfq/hfq 的 2026-09-30=1 覆盖属于旧因子审计参考，不重置本包的因果事件因子。

## 交易和成本

默认初始现金100万元，佣金单边0.5bp、最低佣金0元、滑点1bp；无印花税、现金利息或杠杆。买入100份整手，现金不足下调数量；卖出可含折算后的零股。股票ETF T+1，当日买入不可当日卖出。目标1且已持仓不做再平衡，也不自动分红加仓。期末按市值估值，不强平。未成交目标保留，新的有效信号替换旧目标，记录拒绝原因。

停牌、缺失行情和无成交窗口不能成交。提供涨跌停价时实施限制；**本地源未提供完整历史涨跌停价，因此不声称完整验证这项限制**，覆盖情况见HTML中的“数据诊断与复现信息”。不从次日价格反推历史涨跌停。

可设置 `max_participation=0.01`。超限整笔拒绝，不模拟部分成交；分钟使用执行窗口量作事后容量压力筛选，日频使用上一完整日量，不代表真实盘口撮合。买入整手/成交量容量/极值成交的组合仍是模型假设。

## 每次调用的输出：仅中文 HTML

默认保存为项目当前工作目录内 `output/factors/{factor_name}report.html`。例如因子名“自定义双均线”对应 `output/factors/自定义双均线report.html`。这不是文件系统根目录 `/output`；可通过 `output_root` 指定其他位置。

每次成功调用只写这一个 HTML，不再生成因子子目录、运行编号目录、CSV、Parquet、JSON或策略源码副本。因子名只允许1–80个中文、字母、数字、下划线、连字符，不接受目录穿越。同一因子名再次成功运行会覆盖报告；比较不同频率、区间、参数时，应给因子名加相应后缀。

报告先完整生成，再原子替换目标文件。回测或保存失败会抛出异常，并保留上一份成功报告，不留下临时文件。以前已生成的目录和报告不自动删除。

```python
print(run.report_path)         # 唯一输出文件的绝对路径
print(run.output_dir)          # 兼容属性：报告所在目录，不再是独立运行目录
metrics = run.metrics          # 指标字典仍保留，程序接口字段名保持兼容
factor = run.factor            # 因子检验结果（FactorReport），无因子时为 None
ledger = run.result.ledger     # 逐周期账本
fills = run.result.fills       # 所有成交
trades = run.result.trades     # 往返交易
orders = run.result.orders     # 信号和未成交原因
actions = run.result.actions  # 公司行动
```

HTML完整包含：中文指标表，因子／买入持有／空仓对照，因子检验（IC／ICIR／IC衰减／分组多空）章节，交互图，成交前200笔预览，成本和策略参数，数据质量、涨跌停价覆盖率、数据／信号／事件／因子指纹、解释器及依赖版本。完整逐周期明细保留在返回对象中，报告预览不代替全量明细。

图表标题、图例、坐标轴、悬浮提示、订单原因和指标均为中文；用户给定的因子名、自定义参数标识符及依赖库名按原样显示。HTML嵌入绘图库，可离线打开；隐藏英文图表工具栏，鼠标框选缩放、双击恢复和点击图例仍可使用。价格背景曲线超过12000点时抽样，仓位保留状态变化及相邻点，成交点和统计不抽样。

主要风险指标统一按日终净值：累计/自然日年化收益、波动率、夏普比率、索提诺比率、卡玛比率、最大回撤、恢复时间；252交易日年化风险，零分母在报告显示“不适用”。另含已完成交易胜率、盈亏比、盈利因子、持有天数、换手、暴露、佣金和滑点。直接成本占初始资金比例不是无成本反事实策略的收益差。短区间年化值可能很大。未平仓不计入胜率，期末市值计入净值。

## 因子检验：IC、ICIR、IC 衰减与分组多空

因子检验一律使用**未二值化**的原始因子值，而不是回测用的 0/1 目标仓位。内置策略会自动提供自己的连续因子（`trend` 快慢均线相对价差、`momentum` 回看期收益、`breakout` 通道位置、`mean_reversion` 均值回归标准分，后者为负向因子）；也可以显式传入外部因子序列或因子函数。

```python
run = run_strategy("趋势因子_5min", "trend", frequency="5min", data_root="/path/to/data",
                   config=BacktestConfig(start="2022-01-01"), params={"fast":20,"slow":60},
                   horizons=[1,2,3,5,10,20], quantiles=5, ic_window=240, group_window=252)
print(run.factor.summary())        # 紧凑汇总：IC、ICIR、t 值、多空年化
print(run.factor.ic)              # 每个持有期 × 相关口径的 IC 汇总表
print(run.factor.decay)           # IC 衰减：行=持有期，列=Pearson／Spearman
print(run.factor.groups)          # 分组条件收益、组合绩效与多空对照
print(run.factor.nav)             # 分组与多空组合净值曲线

# 外部因子：与 bars 索引完全对齐的原始因子序列
run_strategy("动量因子", "cash", bars=bars, frequency="5min", factor=close.pct_change(60))
# 或因子函数：factor(bars, **params) -> 原始因子序列
run_strategy("自定义因子", "cash", bars=bars, frequency="5min",
             factor=lambda frame, window=60: frame.signal_close.pct_change(window),
             params={"window":60})
```

口径与参数：

- **IC 与 ICIR**：持有期 h 的 IC 是因子值与之后 h 根有效K线收益的相关系数。计算前按每 h 根抽样一次，未来收益不重叠；再把样本切成等长独立窗口，每个窗口一个 IC。ICIR = 窗口 IC 均值／标准差，t 值 = ICIR × √窗口数，年化 ICIR 按每年独立窗口数折算。同时给出 `pearson` 与 `spearman`（Rank IC）两种口径，以及把所有样本合并计算的“全样本 IC”。
- **ic_window**：每个独立窗口的样本数。默认日频 60（约三个月）、分钟频 240（约1–5个交易日）；窗口越短，窗口 IC 越容易被短周期噪声主导，可能与全样本 IC 符号相反。
- **IC 衰减**：同一因子在不同持有期上的 IC 均值曲线（带 IC 标准差误差棒），用于判断因子有效期。
- **分组多空对照**：分位阈值由滚动窗口计算，只使用当期及之前的样本。每组是“仅在因子落入该组时持有标的、其余时间空仓”的择时组合；多空组合为最高组做多、最低组做空、其余时间空仓。报告给出各组的条件平均收益、组合年化收益、波动、夏普、最大回撤、胜率、期末净值，以及条件平均收益与组序号的秩相关（单调性）。
- **限制**：这是单标的时序口径，不是多标的截面 IC；多空组合只是统计对照，本包只支持多头／空仓，不支持实盘做空。`factor_analysis=False` 可关闭整个因子章节，`run.factor` 与报告中的因子章节会同时消失。

单独使用时可直接调用 `etf_backtest.factor` 中的 `ic_table`、`ic_series`、`quantile_analysis`、`factor_report` 与 `forward_returns`。

## 双区间与参数实验

```python
from etf_backtest import load_market_data, BacktestConfig, run_strategy
bars, events = load_market_data("/path/to/data", "30min")
for label, start in [("全历史", "2016-11-04"), ("2022起", "2022-01-01")]:
    run_strategy("趋势_" + label, "trend", frequency="30min", bars=bars, events=events,
                 config=BacktestConfig(start=start, end="2026-09-30"))
```

每次参数调用的 params 展示在报告中；需保留多个参数结果时，给因子名添加参数后缀。训练/验证/测试应按日期划分，不能用全历史挑出的参数冒充2022版独立样本外。原 `etf_backtest.research.parameter_research` 提供60/20/20和三年训练下一年测试；其批量研究输出是旧接口，需传新建目录，不受新入口仅输出HTML的规则约束。

## 验证与开发文件

- `tests/test_strategy_package.py`：多频量额守恒、午休、尾段、缺失、极值成交、T+1、区间隔离、单文件中文输出、覆盖及失败保留旧报告。
- `tests/test_factor_analysis.py`：完美预知因子的 IC≈1、反向因子的负 IC 与衰减、独立窗口抽样、分组单调性与多空净值、分位阈值不引用未来样本、连续因子不被二值化、内置与自定义因子的报告章节。
- 原 engine/data/research 测试继续覆盖分红、折算、日频无未来信息、信号前缀一致性与会计对账。
- `scripts/validate_strategy_package.py`：六种常用频率真实数据端到端调用，逐笔核对分钟成交参考价，每次只生成因子名对应的HTML报告，验收信息仅打印到终端。
- `examples/new_strategy.py`：可复制修改的策略模板。
- 单独安装包后不需要 Notebook；原 Notebook和原报告不被本次运行覆盖。
