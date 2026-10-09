# ETF Timing Backtest

**南方中证 1000 ETF 择时研究：从因果信号、事件账本到可离线阅读的中文报告。**

[![CI](https://github.com/frisy-qzt/etf-timing-backtest/actions/workflows/ci.yml/badge.svg)](https://github.com/frisy-qzt/etf-timing-backtest/actions/workflows/ci.yml)

Python ≥ 3.10 · v0.4.0 · MIT（代码） · 单标的多头 / 空仓

[快速开始](#快速开始) · [数据样本](examples/data/zz1000/README.md) · [策略 API](docs/strategy-package.md) · [贡献指南](CONTRIBUTING.md)

## 开箱可运行

仓库附带 **512100.SH 日频研究数据：2016-11-04 至 2026-09-30，2,408 行**，以及一次份额合并、两次分红的事件记录。克隆后无需寻找原工作区、下载行情或配置 API key。

- **事件账本**：现金、份额、应收分红、订单、成交与拒单均可追溯。
- **因果执行**：收盘生成信号、下一交易窗口执行；支持 T+1、整手、佣金和滑点。
- **因子检验**：单标的时序 IC / Rank IC / ICIR、衰减和滚动分组。
- **离线报告**：每次高层调用输出一个中文 HTML，图表依赖内嵌。
- **多频率引擎**：支持日频与 1–120 分钟，处理午休和集合竞价；本版本仅附带日频样本。

## 快速开始

```bash
git clone https://github.com/frisy-qzt/etf-timing-backtest.git
cd etf-timing-backtest
python -m venv .venv
source .venv/bin/activate
# Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install -e .
python examples/zz1000_daily_demo.py
```

打开 `output/zz1000/南方中证1000ETF_日频双均线report.html`。默认演示 20 / 60 日均线择时，初始资金 100 万元，佣金单边 0.5bp、滑点 1bp，下一日开盘执行。报告包含买入持有和空仓基准、成交记录、因子检验和数据诊断。

```bash
# 自定义区间与均线窗口
python examples/zz1000_daily_demo.py --start 2022-01-01 --fast 10 --slow 30
# 也可完全使用虚构行情验证 API
python examples/synthetic_demo.py
```

安装依赖需要网络；demo 与核心回测运行时不联网、不下实盘订单。同名报告会在成功后原子替换；比较实验时指定不同 `--output-root`。

## Python 调用

在源码根目录运行：

```python
from etf_backtest import BacktestConfig, run_strategy
from examples.zz1000_daily_demo import load_sample

bars, events = load_sample()
run = run_strategy(
    "中证1000日频动量",
    "momentum",
    frequency="daily",
    bars=bars,
    events=events,
    params={"lookback": 60, "threshold": 0.0},
    config=BacktestConfig(start="2022-01-01", end="2026-09-30"),
)
print(run.report_path)
print(run.metrics)
```

内置策略：`trend`、`breakout`、`momentum`、`mean_reversion`、`buy_hold`、`cash`。自定义策略返回与行情对齐的 0 / 1 / NaN 目标序列；连续因子另行传入。完整协议见 [策略文档](docs/strategy-package.md)。

## 项目结构

```text
etf_backtest/           数据、会话重采样、信号、账本、因子、报告
examples/
  zz1000_daily_demo.py  真实日频演示
  synthetic_demo.py    确定性虚构行情演示
  data/zz1000/         日频 Parquet、事件 CSV、说明和 SHA-256
tests/                 合成账本测试、报告集成及真实样本演示验证
scripts/               可选的数据审计、Notebook 构建与研究工具
docs/                  架构、API、数据契约、规范及发布流程
.github/               CI、Issue 表单和 PR 模板
pyproject.toml         包元数据与依赖、代码检查规则
```

[目录与架构](docs/architecture.md) · [数据契约](docs/data-contract.md) · [文档导航](docs/README.md)

## 数据与研究边界

样本来自维护者已有研究快照，保留原价与因果事件信号视图。成交量单位为份、成交额为元；行情供应商和上游再分发许可未在现有记录中明确，因此**代码 MIT 不覆盖行情数据**。请阅读 [数据来源与权利说明](examples/data/zz1000/README.md)。

项目不是多资产组合或实盘系统，不支持杠杆、真实做空、逐笔订单簿或部分成交。日频用下一开盘价；`run_strategy` 分钟默认买 high / 卖 low，低层 `run_backtest` 默认 VWAP，均为研究价格代理。分组多空仅为统计对照。

历史涨跌停字段存在占位值，字段非空不等于已完整验证涨跌停；自定义策略须自行保证因果性。`BacktestConfig.end` 默认仍为 `2026-09-30`，新研究应显式设置日期。历史收益不代表未来收益，示例不构成投资建议。

## 开发与验证

```bash
python -m pip install -e ".[dev]"
python -m ruff check .
python -m ruff format --check .
python -m unittest discover -s tests -v
python -m build
python -m twine check dist/*
```

CI 覆盖 Linux Python 3.10 / 3.12 / 3.14，检查格式、账本与示例测试、分发包和源码目录外运行。真实样本只增加约 363 KB；无需整个数据湖。wheel 只包含代码，源码包包含示例及样本。

欢迎最小可复现问题和手工可核算的账本反例。提交规范见 [CONTRIBUTING](CONTRIBUTING.md)，版本变化见 [CHANGELOG](CHANGELOG.md)，代码许可证见 [MIT](LICENSE)。
