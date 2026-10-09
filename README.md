# ETF Timing Backtest

中国股票型 ETF 的单标的择时回测与因子分析工具，提供可追溯的交易账本和中文离线报告。

[![CI](https://github.com/frisy-qzt/etf-timing-backtest/actions/workflows/ci.yml/badge.svg)](https://github.com/frisy-qzt/etf-timing-backtest/actions/workflows/ci.yml)

Python ≥ 3.10 · [MIT](LICENSE) · [Releases](https://github.com/frisy-qzt/etf-timing-backtest/releases)

## 功能

- **事件账本**：记录现金、份额、应收分红、订单、成交与拒单原因。
- **成交约束**：收盘信号、下一窗口执行，支持 T+1、整手、手续费和滑点。
- **多频率**：日频及 1–120 分钟，重采样处理午休、集合竞价和尾部窗口。
- **因子分析**：单标的时序 IC / Rank IC / ICIR、衰减和滚动分组。
- **中文报告**：单文件 HTML，内嵌交互图表，可离线阅读。

仓库附带南方中证 1000 ETF（`512100.SH`）日频样本：**2016-11-04 至 2026-09-30，共 2,408 行**，以及一次份额合并和两次分红记录。

## 安装与运行

```bash
git clone https://github.com/frisy-qzt/etf-timing-backtest.git
cd etf-timing-backtest
python -m venv .venv
source .venv/bin/activate
# Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install -e .
python examples/zz1000_daily_demo.py
```

用浏览器打开生成的 `output/zz1000/南方中证1000ETF_日频双均线report.html`。

默认示例采用 20 / 60 日均线，初始资金 100 万元、单边佣金 0.5bp、滑点 1bp，按下一交易日开盘价成交。报告包含买入持有及空仓基准、成交记录、因子分析和数据诊断。

```bash
# 指定区间、均线窗口与报告目录
python examples/zz1000_daily_demo.py --start 2022-01-01 --fast 10 --slow 30 --output-root output/ma10-30

# 使用虚构行情运行相同接口
python examples/synthetic_demo.py
```

## Python API

以下代码可在仓库根目录运行：

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

内置策略为 `trend`、`breakout`、`momentum`、`mean_reversion`、`buy_hold`、`cash`。也可传入返回 0 / 1 / NaN 目标序列的函数或 Series。自有行情通过 `bars=` 和 `events=` 接入，见 [数据契约](docs/data-contract.md)。

## 文档

| 文档 | 内容 |
|---|---|
| [策略 API](docs/strategy-package.md) | 参数、自定义策略、因子检验及结果读取 |
| [数据契约](docs/data-contract.md) | 字段、单位、交易时间和公司行动 |
| [架构](docs/architecture.md) | 模块职责与计算流程 |
| [示例](examples/README.md) | 命令与样本说明 |
| [贡献指南](CONTRIBUTING.md) | 开发环境、代码规范与测试 |

## 模型边界

仅支持单标的多头 / 空仓；不模拟杠杆、订单簿或部分成交。分钟成交使用价格代理：`run_strategy` 默认买 high / 卖 low，`run_backtest` 默认 VWAP。分组多空是统计对照，不代表可实盘做空。

样本涨跌停字段存在异常占位值；自定义策略须自行确保因果性。`BacktestConfig.end` 默认 `2026-09-30`，接入新数据时应显式设置截止日。历史结果不代表未来收益。

## 许可证

代码采用 [MIT](LICENSE)。行情样本不包含在代码许可证内，其来源、单位与使用限制见 [样本说明](examples/data/zz1000/README.md)。
