# 本地研究工作区记录（整理前快照）

> 这是 2026-10-09 工程整理时保存的旧 README（个人绝对路径已替换为示意路径）。数据规模、运行状态、路径与测试数量属于历史记录，并非当前发布承诺。以下链接可能指向未随代码发布的本地资产。当前使用说明请读 [项目首页](../README.md)。

# ETF 多频率行情与择时回测研究库

本目录同时承担两件事：

1. **行情数据湖**：1,793 只 ETF 的 1／5／15／30／60 分钟与日线行情，每个频率都有不复权、前复权（qfq）、后复权（hfq）三个版本，覆盖 2005-02-23 至 2026-09-30，共 18 个 Parquet、14.57 亿行、约 18 GB，并附带逐字段核验与问题筛查报告。
2. **可安装的择时回测包** `etf_backtest`（发行名 `etf-timing-backtest`，当前 0.3.0）：仅做多头／空仓的单标的择时回测，支持 1–120 分钟与日频，含事件驱动账本、全中文单文件 HTML 报告，以及机构口径的因子检验（IC／Rank IC／ICIR／IC 衰减／分组多空）。

回测标的固定为南方中证 1000ETF（`512100.SH`，见 `etf_backtest/data.py` 的 `CODE`）。全部研究不联网、不下载数据、不发实盘订单，原始行情文件只读不改。

## 目录导航

| 路径 | 内容 |
|---|---|
| `1min/` `5min/` `15min/` `30min/` `60min/` `daily/` | 各频率行情，每目录含不复权 Parquet、`qfq/`、`hfq/`、`manifest.json` |
| `ETF日线/` | 原始日线 CSV（`ETF_Daily_2005_2025/` 1,477 个、`ETF_Daily_2026/` 1,673 个） |
| `基金_复权因子/` | 前／后复权因子 ZIP 与解压后的 1,584 个 CSV、`factors.parquet`、`manifest.json` |
| `reports/` | 数据核验、连续性筛查、因子缺失分类与 512100.SH 成交额对账（60 个文件） |
| `data/research/` | 512100.SH 研究数据、公司行动证据 PDF 与指纹、质量报告 |
| `etf_backtest/` | 回测包源码（数据、频率、账本、策略、指标、因子、报告、绘图、批量研究） |
| `notebooks/` | 双频 × 双区间研究 Notebook（已执行，内含完整中文 README） |
| `output/factors/` | 策略包每次调用的唯一产物：`{因子名}report.html` |
| `output/backtest/` | Notebook 研究报告目录，运行脚本后生成 |
| `scripts/` | 数据生成、核验、审计、Notebook 构建与执行、验收脚本 |
| `tests/` | 50 项单元测试（引擎、数据、因子检验、策略包、成交额审计） |
| `docs/` | 策略包开发文档、双频研究实施记录与对抗审查记录 |
| `examples/` | 自定义策略＋自定义因子模板 `new_strategy.py` |
| `.runtime_deps/` | 离线 Plotly 7.1.0 等依赖副本（无网络时可用 `PYTHONPATH=.runtime_deps`） |
| `.cache/` | IPython／Jupyter／matplotlib 缓存 |
| `build/` `dist/` | 打包中间产物；现有 wheel 为 0.2.0 旧构建，需要时重新构建 |

## 快速开始

```bash
cd /path/to/local-data
/opt/miniconda3/bin/python -m pip install -e .                  # 安装回测包（可编辑模式）
/opt/miniconda3/bin/python -m unittest discover -s tests       # 50 项测试
/opt/miniconda3/bin/python examples/new_strategy.py --data-root . --frequency 5min
```

最后一条命令会把报告写到 `output/factors/自定义双均线report.html`，并打印因子检验摘要。本机验证环境为 `/opt/miniconda3/bin/python`（3.14）；若未安装依赖，可先用 `PYTHONPATH=.runtime_deps` 指向项目内离线副本。

## 一、行情数据湖

### 1.1 目录结构与规模

每个频率三个版本：不复权 `5min/ETF_5min.parquet`、前复权 `5min/qfq/ETF_5min.parquet`、后复权 `5min/hfq/ETF_5min.parquet`（日线文件名为 `ETF_daily.parquet`）。每个数据文件旁有 `manifest.json`。

| 频率 | 每版本行数 | 不复权时间范围 | 不复权／qfq／hfq 文件大小 |
|---|---:|---|---|
| 1 分钟 | 368,058,601 | 2005-02-23 09:30 – 2026-09-30 15:00 | 3.9／4.1／4.1 GB |
| 5 分钟 | 73,271,419 | 2005-02-23 09:35 – 2026-09-30 15:00 | 1.0／1.1／1.1 GB |
| 15 分钟 | 24,424,525 | 2005-02-23 09:45 – 2026-09-30 15:00 | 401／449／429 MB |
| 30 分钟 | 12,212,813 | 2005-02-23 10:00 – 2026-09-30 15:00 | 214／242／231 MB |
| 60 分钟 | 6,106,953 | 2005-02-23 10:30 – 2026-09-30 15:00 | 113／130／122 MB |
| 日线 | 1,676,007 | 2005-02-23 – 2026-09-30 | 136／87／83 MB |

每版本合计 485,750,318 行，三个版本共 18 个行情文件、1,457,250,954 行。

### 1.2 字段与类型

公共字段保留源顺序：`datetime`、`code`、`name`、`open`、`close`、`high`、`low`、`volume`、`amount`、`pct_chg`、`amplitude`。

日线另外保留 `pre_close`、`limit_up`、`limit_down`、`nav`、`acc_nav`、`discount_rate`、`total_assets`、`total_shares`，未为匹配分钟字段而删除信息。日线由 3,150 个 CSV 合并而成，覆盖 1,793 个代码。时间为无时区 `timestamp[ms]`，日线日期取 00:00:00；文本字段为 string，价格为 float64；分钟量、额沿用 int64，日线量、额保留源小数用 float64，`total_shares` 为 int64。

### 1.3 复权口径

`复权价格 = 不复权价格 × 有效因子（代码、交易日期、复权版本）`。

有效因子按版本独立判断，前复权与后复权不互相借用：

- 该版本当日有因子记录时使用原值（含负数与零）；该 ETF 在该版本有因子但缺当日记录时用 **1**；整个 ETF 在该版本无因子时价格为 **null**。
- `open`、`close`、`high`、`low` 复权，日线 `pre_close`、`limit_up`、`limit_down` 同乘当日因子；其余字段（成交量、成交额、`pct_chg`、`amplitude`、净值、溢价率、资产、份额）原样保留，比例字段未按复权价重算。
- 不做归一化、不向前填充、不用后复权反推前复权；负因子与零因子按源值计算。负因子会使复权后 `high < low` 反转，报告单列数量，未擅自交换。
- 因子包：`基金_复权因子/qfq/`、`hfq/` 各解压 **1,584** 个 CSV，并提供 `factors.parquet` 与 `manifest.json`；每个复权目录另有 `missing_factors.csv`（reason 区分 `date_missing_use_1` 与 `etf_missing_null`）、`factor_1_dates.csv`、`etf_without_factors.csv`。
- 覆盖范围：源 qfq 因子至 2026-09-30，源 hfq 仅至 2026-09-29；部分 ETF 的 qfq 也缺最后日期。2026-09-30 对"已有因子但缺当日记录"的 ETF 按规则使用 1，复权价等于原价。

### 1.4 原始数据保留

原始分钟 Parquet、`ETF日线/` 原始 CSV、两个因子 ZIP（`基金_复权因子/ETF_复权因子_前复权.zip`、`_后复权.zip`）全部保留。生成脚本通过 `.partial` 文件写完后原子替换目标，重跑只替换已生成的日线及复权文件，不改写任何源文件。

## 二、数据核验与已知问题

### 2.1 核验产出

`reports/`（60 个文件）包含六频率 × 两版本的全量核验与筛查：`数据核验报告.md`、`verification.json`、`verification_progress.json`、`continuity_summary.json`、`source_price_quality.json`、`factor_missing_classification.json`、`因子缺失分类.md`、`512100.SH_成交额核查.md` 与对账 CSV，以及各频率的 `*_bar_continuity.csv`、`*_storage_order_screening.csv`、`*_etfs_without_factors.csv`、`*_day_endpoints.parquet` 等。

核验包含：日线源 CSV 逐字段比对、六个频率全部复权行逐字段比对、独立的代码／日期匹配、因子 ZIP 逐文件 CRC 与字节比对。**结论：全部非价格字段与不复权源完全一致，价格字段等于源价乘有效因子。**

### 2.2 因子缺失

| 版本 | 频率 | 整个 ETF 无因子（代码数／行数） | 缺日期按 1 计算行数 |
|---|---|---:|---:|
| qfq | 日线 | 234 ／ 213,431 | 4,161 |
| qfq | 分钟（各频率） | 112 ／ 17,058–985,203 | 26,382–1,568,075 |
| hfq | 日线 | 234 ／ 213,431 | 5,950 |

完整分类（覆盖前／覆盖内／覆盖后）见 `reports/因子缺失分类.md`。缺日期统一设 1 是既定计算口径，不是沿用前日因子，可能在相邻日期引入价格跳变：qfq 最大有限绝对变化 1,606.48%、hfq 9,907.53%，`*_daily_continuity.csv` 的 `touches_default_factor_1` 列标记此类记录。

### 2.3 其他已披露异常

- **成交额单位不同**：日线 `amount` 为千元、分钟 `amount` 为元，`volume` 均为手（100 份／手）。该结论由成交额比值与 `amount/volume` 隐含均价交叉验证得出，尚无供应商字段说明，其他代码需另行核实。
- **跨频率残差**：512100.SH 日线 2,408 行、60min 9,628 行；统一为元后绝对差异超过 0.1% 的有 11 天，且 2023-06-05 缺 60min 记录，不能全部解释为舍入误差。
- **小时网格修复**：2023-06-05 由 241 条原始分钟记录重建 4 根小时 K 线（成交额 1,054,243,793 元）。
- **质量问题清单**：60min 有 3 处 VWAP 越界、7 处零成交；日线 1 处零成交。分钟源部分日期按文件存储顺序倒序，数据保留原行序，`*_storage_order_screening.csv` 单列筛查结果。
- **筛查不等于错误**：分红、拆分、停牌、真实行情与近零价格都会触发连续性阈值，报告只作提示。

### 2.4 512100.SH 公司行动（3 项，均有官方原文与哈希）

| 事件 | 公告 | 权益登记日 | 生效／除息日 | 支付／复牌日 | 参数 |
|---|---|---|---|---|---|
| 份额合并 | 2022-08-24 首次、09-01 提示、09-05 结果 | 2022-09-01 | 2022-09-02 | 2022-09-05 复牌 | 每旧份换 0.36555 份，账户总份额向上取整 |
| 现金分红 | 2025-01-10 | 2025-01-14 | 2025-01-15 | 2025-01-20 | 每份 0.037 元 |
| 现金分红 | 2026-01-14 | 2026-01-16 | 2026-01-19 | 2026-01-22 | 每份 0.041 元 |

证据、URL 与 SHA-256 见 [EVENT_EVIDENCE.md](../data/research/EVENT_EVIDENCE.md)、[corporate_actions.csv](../data/research/corporate_actions.csv)、[evidence_manifest.json](../data/research/evidence_manifest.json)。分红公告未说明分位舍入规则，CSV 标为 `not_specified_in_notice`；2022 年 7 月曾公告后取消的拆分不入账。

## 三、择时回测包 etf_backtest

### 3.1 模块地图

| 模块 | 职责 |
|---|---|
| `data.py` | 读取本地源、构造因果事件复权研究视图与质量报告 |
| `frequency.py` | 会话感知的分钟重采样、右端标记、集合竞价归入首根 |
| `engine.py` | 现金／份额事件账本，`run_backtest` 返回 ledger／orders／fills／trades／actions |
| `strategies.py` | 四类因果策略、参数网格，以及**未二值化**的 `raw_factor` |
| `factor.py` | 因子检验：独立窗口 IC／Rank IC／ICIR／IC 衰减／分组多空 |
| `metrics.py` | 自然日年化与统一按日终净值的风险指标 |
| `api.py` | `run_strategy` 入口：回测＋因子检验＋单文件报告 |
| `report.py` | 全中文离线 HTML 报告（原子替换，磁盘只留一个文件） |
| `research.py` | 批量固定回测、60/20/20 分割、滚动样本外与成本／容量敏感性 |
| `plots.py` | 旧版双频研究的交互图与静态总览 |
| `api.py` / `__init__.py` | `StrategyRun`、`run_strategy`、`FactorReport` 等公共接口 |

### 3.2 最短调用

```python
from etf_backtest import BacktestConfig, run_strategy

run = run_strategy(
    factor_name="均线趋势_5min",          # 只允许 1–80 个中文／字母／数字／下划线／连字符
    strategy="trend",                    # 或传入函数、或对齐的 0/1 目标序列
    frequency="5min",                    # daily / 1min … 120min
    data_root="/path/to/local-data",
    params={"fast": 20, "slow": 60},     # 参数单位是当前频率的 K 线根数
    config=BacktestConfig(start="2022-01-01", end="2026-09-30"),
)
print(run.report_path)   # output/factors/均线趋势_5minreport.html
print(run.metrics)       # 绩效指标字典
print(run.factor.ic)     # 因子检验结果（未二值化因子值）
```

内置策略：`trend`、`breakout`、`momentum`、`mean_reversion`、`buy_hold`、`cash`。每次调用独立以现金启动，不继承其他运行的状态。

### 3.3 频率与成交时间轴

- 支持 1–120 分钟任意整数分钟与日频；每个半日 120 分钟，不能整除 120 的频率（如 7min、90min）在 11:30、15:00 留一个较短尾段。
- 09:30 集合竞价归入首根窗口；1min 首根 09:31 含 09:30 与 09:31 两条源记录，之后右端标记，不重复边界。
- 信号在本根结束后产生，**下一个交易窗口**执行：分钟默认买入取该窗口最高价、卖出取最低价（`intraday_price="extreme"`），可显式改为 `"vwap"`（amount/volume）或 `"high"`；日频为下一交易日开盘价。买卖各计 1bp 滑点，因此成交价可能超出原始高低区间。
- 股票型基金执行次交易日可卖（T+1）；停牌、涨跌停、行情缺失、现金不足或不足一手都会记录拒单原因。

### 3.4 因子检验：IC、ICIR、IC 衰减与分组多空

因子检验一律使用**未二值化**的原始因子值，而不是回测用的 0/1 目标仓位。内置策略会自动提供连续因子（`trend` 快慢均线相对价差、`momentum` 回看期收益、`breakout` 通道位置、`mean_reversion` 标准分，后者为负向因子），也可通过 `factor=` 传入序列或 `factor(bars, **params)` 函数。

| 检验 | 口径 |
|---|---|
| IC／Rank IC | 因子值与之后 h 根有效 K 线收益的 Pearson／Spearman 相关；按每 h 根抽样，未来收益不重叠 |
| ICIR | 把非重叠样本切成等长**独立窗口**，每窗一个 IC；ICIR = 窗口 IC 均值／标准差，t 值 = ICIR×√窗口数，另给年化 ICIR 与全样本 IC |
| IC 衰减 | 同一因子在 `horizons=[1,2,3,5,10,20]` 上的 IC 均值曲线（带 IC 标准差误差棒） |
| 分组多空 | 滚动分位阈值（**只用当期及之前样本**）分 5 组；每组是"仅在该组持有、其余空仓"的择时组合，输出条件平均收益、年化、波动、夏普、回撤、胜率、期末净值与单调性；多空组合为最高组做多、最低组做空 |

`ic_window` 默认日频 60、分钟频 240 个样本（窗口过短时窗口 IC 会被短周期噪声主导）；`quantiles` 默认 5；`factor_analysis=False` 可整体关闭。这是单标的时序口径而非多标的截面 IC，多空组合仅为统计对照，本包不支持实盘做空。完整参数、口径与限制见 [策略包文档](../docs/strategy-package.md)。

### 3.5 输出：只有一个全中文 HTML

默认保存为 `output/factors/{因子名}report.html`（可用 `output_root` 改路径，不是文件系统根目录的 `/output`）。报告先完整生成再原子替换，失败保留上一份成功报告且不留临时文件；不再产生运行编号目录、CSV、Parquet、JSON 或源码副本。当前该目录只有报告文件，另有上一次六频验收遗留的 `index.html` 与 `validation.json`（其中的链接指向已清理的旧目录）：

```python
run.report_path        # 唯一输出文件的绝对路径
run.output_dir         # 兼容属性：报告所在目录
run.factor             # FactorReport，无因子时为 None
run.result.ledger      # 逐周期账本（orders／fills／trades／actions 同理）
```

报告含：中文指标表、因子／买入持有／空仓对照、因子检验章节、10 类交互图、成交前 200 笔预览、成本与参数、数据质量与涨跌停覆盖率、数据／信号／事件／因子指纹、解释器与依赖版本。绘图库内嵌、可离线打开，无外部资源引用。

## 四、双频 × 双区间研究 Notebook

研究入口 [notebooks/中证1000ETF_双频双区间回测.ipynb](../notebooks/中证1000ETF_双频双区间回测.ipynb)（28 个单元格，含 9 节中文 README 与已执行输出）：全历史 2016-11-04–2026-09-30 与 2022 年起两个区间 × 日频下一日开盘／60 分钟下一交易小时 VWAP × 4 策略＋2 基准 = 24 组固定回测，16 组独立测试集，72 个滚动折；本金 100 万元、佣金万 0.5、最低 0 元、滑点 1bp、严格 T+1。

```bash
/opt/miniconda3/bin/python -m pip install -r requirements-backtest.txt
/opt/miniconda3/bin/python -m jupyter lab notebooks/中证1000ETF_双频双区间回测.ipynb
/opt/miniconda3/bin/python scripts/execute_backtest_notebook.py   # 重跑并导出阅读版 HTML
/opt/miniconda3/bin/python scripts/build_backtest_notebook.py     # 重建模板（会清除旧输出）
```

Notebook 顶部可改日期、费用与固定策略参数，改后需从干净内核完整重跑。运行后写入 `output/backtest/`：`report.html`（四组切换、每组 11 类图）、`notebook.html`（阅读版）、`overview.png`（静态总览）、`summary.csv`、`selected_oos.csv`、`walk_forward.csv`、`environment.json`、`notebook_validation.json`；`scripts/validate_backtest_outputs.py` 另生成 `delivery_validation.json`。该目录尚未生成（当前 `output/` 下只有 `factors/`），按上述命令重新运行即可。

## 五、脚本清单

| 脚本 | 用途 | 常用参数 |
|---|---|---|
| `prepare_etf.py` | 由原始 CSV／分钟 Parquet 生成日线与精确日期复权行情 | `--frequencies daily 60min`、`--reuse-inputs` |
| `verify_etf.py` | 逐字段核验复权结果，输出连续性与异常筛查 | `--frequencies`、`--finalize-only` |
| `audit_source_prices.py` | 单独统计源数据非正收盘价，不混入复权价 | — |
| `audit_turnover.py` | 单只 ETF 的 60 分钟成交额与日线源单位对账 | `--code 512100.SH`、`--daily-amount-scale` |
| `validate_strategy_package.py` | 六频率真实数据端到端验收，逐笔核对分钟成交参考价 | — |
| `validate_backtest_outputs.py` | 校验已保存 Notebook、实验矩阵、账本与离线 HTML | — |
| `build_backtest_notebook.py` | 用 nbformat 重建研究 Notebook 模板 | — |
| `execute_backtest_notebook.py` | 执行全部单元格并导出阅读版 HTML | `--html-only` |

数据重建与核验（只读源、原子替换产物）：

```bash
/opt/miniconda3/bin/python scripts/prepare_etf.py
/opt/miniconda3/bin/python scripts/audit_source_prices.py
/opt/miniconda3/bin/python scripts/verify_etf.py
```

大数据读取建议使用 dataset 谓词下推，避免一次加载全部行情：

```python
import pyarrow.dataset as ds
table = ds.dataset('5min/qfq/ETF_5min.parquet', format='parquet').to_table(
    filter=ds.field('code') == '512100.SH')
```

## 六、测试与验收

```bash
/opt/miniconda3/bin/python -m unittest discover -s tests -v     # 50 项，当前全部通过
/opt/miniconda3/bin/python scripts/validate_strategy_package.py # 六频率真实数据验收
```

| 测试文件 | 项数 | 覆盖重点 |
|---|---:|---|
| `test_backtest.py` | 15 | 日频开盘无未来信息、小时 VWAP 与跨午休 T+1、分红应收、折算停牌、涨跌停、区间隔离、前缀不变性 |
| `test_factor_analysis.py` | 11 | 完美预知因子 IC≈1、反向因子负 IC 与衰减、独立窗口抽样、分组单调性与多空净值、分位阈值因果性、连续因子不被二值化 |
| `test_strategy_package.py` | 9 | 多频量额守恒、午休与尾段、极值成交、单文件中文输出、失败保留旧报告、参数生效 |
| `test_research_data.py` | 6 | 集合竞价只计一次、千元／手单位、折算因果性、缺失日四根不可交易 |
| `test_turnover_audit.py` | 5 | 单位换算、缺失日期不记 0、重复键报错、部分空值不判整日 |
| `test_factor_lookup.py` | 4 | 负因子／零因子、缺日期按 1、日内时间归属、按版本区分存在性 |

## 七、文档索引

| 文档 | 内容 |
|---|---|
| [docs/strategy-package.md](../docs/strategy-package.md) | 策略包安装、调用、因子检验、策略协议、输出与限制（同时是包的分发 README） |
| [docs/strategy-package-review.md](../docs/strategy-package-review.md) | 策略包两路对抗审查与修复记录 |
| [docs/backtest-implementation.md](../docs/backtest-implementation.md) | 双频双区间研究的实施记录与固定语义 |
| [docs/backtest-review.md](../docs/backtest-review.md) | 双频研究的对抗审查、模型边界与交付验收 |
| [data/research/EVENT_EVIDENCE.md](../data/research/EVENT_EVIDENCE.md) | 512100.SH 三次公司行动的官方原文核验 |
| [data/research/quality_report.json](../data/research/quality_report.json) | 研究数据质量、单位、修复与源指纹 |
| [reports/数据核验报告.md](../reports/数据核验报告.md) | 六频率全量核验与连续性筛查结论 |
| [reports/因子缺失分类.md](../reports/因子缺失分类.md) | 缺日期按 1 与整表无因子的完整分类 |
| [reports/512100.SH_成交额核查.md](../reports/512100.SH_成交额核查.md) | 成交额单位与逐日对账 |

## 八、已知边界

- **主结果是价格代理**：开盘／VWAP／极值成交都是可复现的价格基准，不模拟逐笔订单簿；容量上限是整笔拒单情景，不是部分成交模型。早期小成交量时可能出现超过市场量的订单，必须同时阅读参与率与容量压力结果。
- **滚动折连乘是评估指数**：各折独立空仓初始化，折间连乘不代表连续实盘持仓。
- **因子检验是单标的时序口径**：不是多标的截面 IC；独立窗口之间不重叠，但窗口越短，窗口 IC 与全样本 IC 的差异可能越大。多空组合不可实盘做空。
- **单位与缺失**：日线成交额为千元、分钟为元，跨频率比较前必须换算；日线缺因子 ETF 为 234 个（213,431 行）、分钟频率 112 个；hfq 因子覆盖至 2026-09-29。
- **数据限制**：本地源没有完整历史涨跌停价，报告按覆盖率披露，未把未提供的限制声称为已模拟；分钟原始 ZIP 不在本目录，分钟数据只能与本地其他频率及官方事件交叉核验。
- **自定义策略**：需自行核验因果性（可用前缀不变性测试），包只保证内置策略向后观察。
- **打包产物**：`dist/etf_timing_backtest-0.2.0-py3-none-any.whl` 与 `build/` 早于当前 0.3.0 代码（尚不含 `factor.py`），需要分发时用
  `/opt/miniconda3/bin/python -m pip wheel . --no-deps --no-build-isolation --wheel-dir dist` 重新构建。
