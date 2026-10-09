"""Build the documented research notebook using nbformat, without hidden state."""

import ast
from pathlib import Path

import nbformat as nbf

ROOT = Path(__file__).resolve().parents[1]
cells = []


def md(text):
    cells.append(nbf.v4.new_markdown_cell(text.strip()))


def code(text):
    cells.append(nbf.v4.new_code_cell(text.strip()))


md("""# 中证1000ETF：双频、双区间择时研究

**南方中证1000ETF · 512100.SH · 数据截至2026-09-30**

本Notebook是研究入口、完整README和可复现实验记录。它从本地原始数据建立标准化研究视图，核算真实现金/份额/分红应收，比较四类多头/空仓策略，并运行独立测试集、滚动验证、成本和容量实验。

**交付内容**：24组固定回测（4策略＋2基准 × 2频率 × 2日期版本）、16组参数选择后的测试集、逐年滚动样本外、完整交易账本、交互HTML和CSV/Parquet明细。

研究结论以本次实际执行输出为准。给定价格的主回测不限制成交容量，早期低流动性阶段存在订单大于市场成交量的情况，必须结合容量对照阅读。""")
md("""## README · 1. 安装与运行

在项目根目录启动：
```bash
python -m pip install -r requirements-backtest.txt
python -m jupyter lab notebooks/中证1000ETF_双频双区间回测.ipynb
```
请先在虚拟环境中执行 `python -m pip install -e ".[notebook]"`。Notebook兼容原工作区的可选 `.runtime_deps/`，公开安装无需该目录。

选择 **Restart Kernel and Run All**。数据准备会筛选目标ETF，避免把全市场分钟数据读入内存；首次完整参数研究可能需要数分钟。所有计算在本地完成，重跑Notebook不联网、不下单。

原始输入：`daily/ETF_daily.parquet`、`60min/ETF_60min.parquet`、必要时`1min/ETF_1min.parquet`、`ETF日线/*/512100.SH.csv`、`基金_复权因子/*/512100.SH.csv`。公司行动证据在`data/research/`，修改原始数据或事件后请从头重跑。

输出：`data/research/`为研究行情、事件和质量报告；`output/backtest/日期版本/频率/策略/`保存账本和成交；根输出目录保存汇总CSV、完整交互HTML及独立分组HTML。重跑只更新这些派生结果，不覆盖原始行情。

测试命令：`python -m unittest discover -s tests -v`。""")
md("""## README · 2. 配置与回测矩阵

|版本|起始|截止|初始化|
|---|---|---|---|
|full 全历史|2016-11-04|2026-09-30|100万元、空仓|
|since_2022|2022-01-01，实际首交易日2022-01-04|2026-09-30|重新初始化100万元、空仓|

两版各运行daily与60min。2022版可使用更早行情预热指标，但此前的现金、持仓、订单和收益一律不继承；首个信号必须在区间内生成。全历史前段预热不足时保持空仓。

默认单边佣金0.00005=万0.5=0.5bp，最低佣金0元；单边滑点0.0001=1bp；买入100份整手。初始资金、日期、费用、诊断展示分组均在下一格修改。""")
code("""from pathlib import Path
import sys, os, json, platform, importlib.metadata
ROOT = Path.cwd()
if not (ROOT / 'daily').exists():
    ROOT = ROOT.parent
assert (ROOT / 'daily/ETF_daily.parquet').exists(), '请从项目或notebooks目录运行'
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / '.runtime_deps'))
os.environ.setdefault('MPLCONFIGDIR', str(ROOT / '.cache/matplotlib'))
import numpy as np
import pandas as pd
from IPython.display import display, HTML, Image, Markdown
from etf_backtest.engine import BacktestConfig, run_backtest
from etf_backtest.data import prepare_data
from etf_backtest.strategies import signal, DEFAULTS, LABELS
from etf_backtest.metrics import performance, daily_equity, daily_returns
from etf_backtest.research import run_matrix, parameter_research, split_dates
from etf_backtest.plots import equity_figure, static_overview, export_report, PLOT_CONFIG

RANGES = {'full': ('2016-11-04', '2026-09-30'),
          'since_2022': ('2022-01-01', '2026-09-30')}
CONFIG = BacktestConfig(initial_cash=1_000_000, commission=0.00005,
                        minimum_commission=0, slippage=0.0001,
                        lot_size=100, max_participation=None)
FIXED_PARAMS = {name: dict(params) for name, params in DEFAULTS.items()}
# 例如 FIXED_PARAMS['trend'] = {'fast': 10, 'slow': 40}
VIEW = ('since_2022', '60min')  # 后续详细图表分组；报告可切换全部四组
OUTPUT = ROOT / 'output/backtest'
OUTPUT.mkdir(parents=True, exist_ok=True)
pd.set_option('display.max_columns', 20)
pd.set_option('display.max_rows', 30)
versions = {name: importlib.metadata.version(name) for name in
            ['numpy','pandas','pyarrow','plotly','nbformat','nbclient','matplotlib']}
(OUTPUT/'environment.json').write_text(json.dumps({'python':sys.version, 'packages':versions}, indent=2))
display(pd.DataFrame([{'package':k,'version':v} for k,v in versions.items()]))""")
md("""## README · 3. 数据字段、复权与修复

|研究字段|含义|
|---|---|
|open/high/low/close|未复权原始元/份价格，执行和现金账本使用|
|amount / volume|统一为元/份；日线原amount乘1000，所有源volume乘100|
|vwap|amount/volume；零成交量时为空|
|signal_close/high/low|仅使用已生效公司行动构造的因果复权价格|
|event_factor|从1开始的前向累计复权因子，不含未来事件|
|valid / missing_bar|是否可生成信号、是否缺失源窗口|
|mark_close|无法交易时可用的估值代理，不可据此成交|

**三个实际公司行动**：2022-09-02份额合并（每旧份变0.36555份，账户总份额向上取整、全天停牌）；2025-01-15除息每份0.037元、01-20支付；2026-01-19除息每份0.041元、01-22支付。现金股利权益取登记日收盘持仓，除息记应收、支付转现金；公告未明确分位舍入，本框架保留浮点精度。

前向复权：份额折算时`factor *= share_ratio`；现金分红时`factor *= previous_close / (previous_close - dividend)`。因子只在事件生效时变化，避免原始qfq的未来事件影响历史阈值。信号价格的尺度不等于证券真实成交价格。

2022-09-02源因子缺失：对仍处于折算前尺度的停牌占位原价沿用09-01源因子，仅作核验；研究估值使用上次真实收盘/0.36555，禁止成交和新信号。2026-09-30源qfq/hfq研究参考列均按用户要求设1。**该参考修复不重置事件累计因子**，否则会产生虚假跳变。

2023-06-05小时数据由原始1分钟记录重建；首窗口包括09:30集合竞价，之后采用右端标记、不重复边界。完整性不足则保留缺失并禁止交易。少量量额残差保留报告，不强行缩放；VWAP超出原始价格区间±0.001的窗口禁止成交。""")
code("""data, events, quality = prepare_data(ROOT)
display(events[['kind','record_date','ex_date','pay_date','ratio','cash_per_share','rounding','source_url']])
display(pd.DataFrame(quality['issues']))
display(pd.DataFrame(quality['hourly_repairs']))
print('原始日线逐值核验:', quality['daily_matches_original_csv'])
print('日线行数:', quality['daily_rows'], '小时研究网格:', quality['hourly_grid_rows'])
print('金额统一后残差超过0.1%的日期:', quality['amount_residual_over_0_1_percent_days'])
display(pd.read_csv(ROOT/'data/research/repairs.csv'))""")
md("""## README · 4. 成交时序和账本

|信号时点|成交窗口/价格|结算时点|
|---|---|---|
|日频T日15:00|T+1交易日开盘价|T+1日09:30|
|10:30|10:30之后至11:30的成交VWAP|11:30|
|11:30|13:00—14:00的VWAP|14:00|
|14:00|14:00之后至15:00的VWAP|15:00|
|15:00|下一交易日09:30—10:30的VWAP|次日10:30|

同一时点先结算此前信号的订单，再生成新信号。因此不会使用当前K线生成信号并在同根成交。VWAP窗口执行用单笔等价成交表示，窗口结束前不把买入份额记入账户；收盘估值仅计入`数量×(收盘价−成交价)`的实际价差，不重复叠加整根收益。

**T+1**：当天新买入份额当天不可卖；次交易日解锁。若14:00买入、15:00出现空仓目标，最早次日首小时执行卖出。无效窗口不生成新信号；有效新信号替换未执行目标。未成交目标等待后续可执行窗口；期末待执行目标记录为`end_of_range`，不越界成交、不强制平仓。

买入价`参考价×(1+滑点)`，卖出价`参考价×(1−滑点)`；佣金`max(成交金额×费率, 最低佣金)`。整手数量在预留成本后向下取整。持有状态不因残余现金或分红自动加仓，只在空仓转多头时买入。折算向上取整后可能出现零股，卖出清仓时允许全部卖出。

日频开盘执行只使用有效开盘报价、已公告停牌状态及事前价格限制，不使用当天收盘质量决定是否开盘成交。小时停牌/零成交/无有效价格不成交。日频开盘触及不利方向涨跌停时保守拒绝；小时只在整个窗口封于不利涨跌停时拒绝。无订单簿，不能验证开盘排队成交；小时成交价是VWAP代理。主结果不限制容量，但每笔记录参与率；容量实验日频使用前一完整日成交量，小时使用已执行窗口总量，后者属于事后压力筛查，不是精确撮合。

权益恒等式：`equity = cash + shares × mark + dividend_receivable`。分红应收不能用于提前买入，卖出后仍会在支付日转现金。""")
md("""## README · 5. 策略接口与固定参数

`signal(bars, name, params)`返回与行情索引一致的0/1目标仓位；`run_backtest(bars, targets, config, events)`执行交易。策略不要自行shift成交延迟，引擎已负责下一窗口执行。

|策略|入场/退出|固定参数（单位：当前频率K线根数）|
|---|---|---|
|trend|快均线高于慢均线持有，否则空仓|20 / 60|
|breakout|收盘突破此前N根最高价入场，跌破此前M根最低价退出|40 / 20，通道明确排除当前根|
|momentum|过去N根收益大于阈值持有|60 / 0|
|mean_reversion|滚动标准分低于阈值入场，回到均值退出|20 / -1.5 / 0|

指标窗口按有效K线计数，停牌或缺失根不生成信号，也不会重置已有指标预热。日频20根通常是20个交易日，小时20根通常约5个交易日；两频默认并非相同自然时间尺度。四策略是框架演示与可比较研究，不是已证明最优参数。两日期版本使用同一组固定参数。

买入持有也遵守首个区间内收盘信号、下一窗口成交及交易成本；始终空仓收益为0。全历史策略预热期保留空仓，因此基准可能比需预热的策略更早入场，评估日期仍相同。""")
code("""results, summary = run_matrix(data, events, OUTPUT, CONFIG, RANGES, FIXED_PARAMS)
columns = ['version','frequency','strategy','total_return','annual_return','max_drawdown',
           'sharpe','closed_trades','average_exposure','max_participation']
display(summary[columns].round(4))
static_overview(results, OUTPUT/'overview.png')
display(Image(filename=str(OUTPUT/'overview.png')))""")
md("""## README · 6. 指标口径

- 累计收益=`期末权益/初始资金−1`；CAGR按覆盖自然日/365.25年化，短区间年化可能放大。
- 日终收益由相邻交易日日终权益计算，首日相对初始资金；Sharpe=`日均收益/日收益样本标准差×√252`，无风险利率0。两个频率统一使用日终收益。
- Sortino下行波动=`sqrt(mean(min(日收益,0)^2))×√252`；Calmar=`CAGR/abs(最大回撤)`。零波动或无回撤时相应比率为空，不伪造0。
- 回撤峰值包含初始资金。输出最长未创新高交易日数、最大回撤谷底至恢复的自然日数，以及是否仍未恢复；未恢复时恢复天数为空。
- 交易胜率、盈亏比、持仓时长只统计已平仓往返交易；交易净收益包含买卖佣金、滑点及登记获得的分红。未平仓交易单独保留浮盈亏。
- 年化换手=`买卖成交金额合计/平均日终权益/自然年数`，双边均计。平均仓位按当前频率的估值观测等权，不是连续时间积分。
- 成本金额占初始资金和无成本对照分别提供；前者不是严格的复利收益差，后者还包含整手取整导致的路径差异。
- 年/月收益是每日收益复合，首尾不完整年度/月度按实际覆盖计算。
""")
md("""## 研究 · 四组净值与回撤

交互图可缩放、框选、悬停查看精确值，点击图例隐藏/显示策略。报告文件内嵌Plotly，无需网络。""")
code("""from plotly.offline import get_plotlyjs
display(HTML('<script>'+get_plotlyjs()+'</script>'))
def show_figure(fig):
    display(HTML(fig.to_html(full_html=False, include_plotlyjs=False,
                            config=PLOT_CONFIG)))
for version in RANGES:
    for frequency in data:
        display(Markdown(f'### {version} / {frequency}'))
        group = {key[2]:r for key,r in results.items() if key[:2]==(version,frequency)}
        show_figure(equity_figure(group))""")
md("""## README · 7. 参数选择与样本外

每个日期版本按交易日独立60%训练、20%验证、20%测试，同一天的小时记录不会被拆到不同集合。各阶段独立资金与空仓；指标可用阶段之前的数据预热。

训练集以日终Sharpe选前三候选，验证集从三者中选一，最后只在测试集运行所选参数；分数相同按固定网格顺序选取。训练集热图不包含测试集遍历，测试结果不可用于再选参数。

滚动验证每年使用此前完整三自然年训练、下一年测试。全历史首折测试2020年；2022版首折测试2025年，2026年测试只到09-30。各折独立空仓，图中的连乘是折间评估指数，不模拟连续持仓、切换和强平。

本步骤计算量较大，默认完整执行；网格定义在`etf_backtest/strategies.py`。不使用随机切分，不将全历史版选中的参数直接用于2022版测试。""")
code("""split_table = []
for version, (start,end) in RANGES.items():
    for stage,(a,b) in split_dates(data['daily'].loc[start:end].index).items():
        split_table.append({'version':version,'stage':stage,'start':a,'end':b})
display(pd.DataFrame(split_table))
studies, oos_results, fold_results = parameter_research(data, events, OUTPUT, CONFIG, RANGES, FIXED_PARAMS)
display(studies['selected_oos'][['version','frequency','strategy','params','start','end',
                               'total_return','max_drawdown','sharpe']].round(4))""")
md("""## 研究 · 详细图表与独立报告

下方展示配置`VIEW`选择的分组。四组完整图表均保存到交互报告，包含月度、年度、仓位、成交标记、收益分布、训练集参数热图、成本和容量敏感性、独立测试集与滚动验证。""")
code("""figures = export_report(results, summary, studies, oos_results, fold_results, quality, OUTPUT)
print('综合交互报告:', OUTPUT/'report.html')
print('独立分组报告:', [p.name for p in sorted(OUTPUT.glob('report_*.html'))])""")
md("""### 实际仓位与最近一年交易

仓位反映实际持仓而非信号；T+1、停牌及拒单可能使两者不同。交易图以未复权价格标出最近一年买卖成交，避免合并前后原价尺度混合。""")
code("""show_figure(figures[VIEW]['exposure'])
show_figure(figures[VIEW]['trades'])""")
md("""### 月度、年度和交易分布

关注收益是否集中于少数阶段。交易分布仅包含已平仓交易，不把期末浮盈亏当作已实现胜率。""")
code("""show_figure(figures[VIEW]['monthly'])
show_figure(figures[VIEW]['annual'])
show_figure(figures[VIEW]['distribution'])""")
md("""### 参数、交易成本与成交容量

训练热图用于识别参数是否依赖孤立尖峰。成本实验保持信号参数不变；容量实验按1%、10%、100%量上限整笔拒绝超限订单，待后续窗口或信号变化，属于保守压力情景。日频分母为前一完整日成交量，小时分母为执行窗口成交量；两者都无法精确代表订单簿可成交量。""")
code("""show_figure(figures[VIEW]['parameters'])
show_figure(figures[VIEW]['costs'])
show_figure(figures[VIEW]['capacity'])
cost = studies['cost_sensitivity']
cost = cost[(cost.version==VIEW[0]) & (cost.frequency==VIEW[1])]
display(cost[['strategy','commission_bp','slippage_bp','total_return','commission_yuan','slippage_yuan']].round(4))""")
md("""### 独立测试集与滚动样本外

不要只看固定参数全区间的最佳曲线。独立测试与滚动验证才回答“只使用此前资料选择参数后，未来阶段如何表现”。不同研究区间之间存在重叠，不是相互独立的两次统计实验。""")
code("""show_figure(figures[VIEW]['oos'])
show_figure(figures[VIEW]['walk_forward'])
wf = studies['walk_forward']
display(wf[(wf.version==VIEW[0]) & (wf.frequency==VIEW[1])]
        [['strategy','year','params','train_start','train_end','total_return','max_drawdown','sharpe']].round(4))""")
md("""## README · 8. 接入自己的择时信号

下面演示10/30均线；只生成目标，不自行模拟成交。将参数固定后再进入独立测试。策略不得使用负shift、居中滚动、未来最高最低、全样本标准化或测试集反复调参。""")
code("""from dataclasses import replace
bars = data[VIEW[1]]
price = bars.loc[bars.valid, 'signal_close']
fast = price.rolling(10).mean()
slow = price.rolling(30).mean()
custom_target = (fast > slow).astype(float).reindex(bars.index)
start, end = RANGES[VIEW[0]]
custom_result = run_backtest(bars, custom_target,
                             replace(CONFIG, start=start, end=end), events)
display(pd.DataFrame([performance(custom_result)]).round(4))
display(custom_result.fills.head(8))""")
md("""## README · 9. 明细、质量验收与复现

`ledger`包含cash、shares、sellable、receivable、mark、equity、target、exposure；`orders`记录目标和执行/拒绝原因；`fills`包含信号时点、窗口、参考价、执行价、数量、佣金、滑点成本、参与率；`trades`为往返交易；`actions`为公司行动账务。

核心测试覆盖下一根执行、VWAP与收盘收益、不足整手、零最低佣金、T+1、折算停牌、分红应收、缺失窗口、区间重置及信号前缀不变性。图表和汇总都由相同账本计算。下方额外核对全部24组资产恒等式与时间顺序。

新增数据超过2026-09-30前，必须先更新官方公司行动和数据截止规则。回测不连接券商，无税率外推、无收益保证；ETF折溢价、市场冲击和真实排队成交不由OHLCV完整刻画。""")
code("""checks = []
for key, result in results.items():
    l = result.ledger
    expected = l.cash + (l.shares*l.mark).fillna(0) + l.receivable
    assert np.allclose(l.equity, expected)
    assert (l.cash >= -1e-6).all() and (l.sellable >= 0).all()
    assert l.index.min().normalize() >= pd.Timestamp(RANGES[key[0]][0])
    if not result.fills.empty:
        assert (result.fills.timestamp > result.fills.signal_time).all()
    checks.append({'version':key[0],'frequency':key[1],'strategy':key[2],
                   'accounting_ok':True,'fills':len(result.fills)})
display(pd.DataFrame(checks))
(OUTPUT/'notebook_validation.json').write_text(json.dumps({'matrix_runs':len(checks),
    'all_accounting_checks_passed':True,'range_count':len(RANGES),'frequency_count':len(data),
    'selected_oos_count':len(oos_results),'walk_forward_fold_count':len(fold_results)},indent=2))
display(Markdown('**执行完成。** 完整图表见 `output/backtest/report.html`；交易明细与参数研究CSV保存在同目录。'))""")

notebook = nbf.v4.new_notebook(
    cells=cells,
    metadata={
        "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
        "language_info": {"name": "python"},
        "title": "中证1000ETF双频双区间回测",
    },
)
nbf.validate(notebook)
for i, cell in enumerate(notebook.cells):
    if cell.cell_type == "code":
        ast.parse(cell.source, filename=f"notebook cell {i + 1}")
folder = ROOT / "notebooks"
folder.mkdir(exist_ok=True)
path = folder / "中证1000ETF_双频双区间回测.ipynb"
nbf.write(notebook, path)
print(path)
