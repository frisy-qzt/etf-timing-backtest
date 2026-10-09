# 目录与模块架构

## 设计边界

保留现有平铺包布局，不改变公共导入路径。源码、演示样本和可复现测试构成开源项目；仅 `examples/data/zz1000/` 是本版本明确附带的真实日频样本，其余数据湖和运行产物为独立本地资产。目录整理不改写原始行情，也不修改回测经济口径。

```text
bars / 本地 Parquet + 已核验事件
                  ↓
data.py → frequency.py → strategies.py / 自定义策略
                  ↓               ↓
                  └──── engine.py ┘
                             ↓
                     metrics.py + factor.py
                             ↓
                       api.py → report.py
```

| 模块 | 责任 |
|---|---|
| `data.py` | 固定本地标的适配、源单位归一化、已发生事件的信号价格视图 |
| `frequency.py` | 1–120 分钟会话重采样、缺失窗口标记、本地行情读取 |
| `engine.py` | 现金、份额、应收、订单、成交及事件账本 |
| `strategies.py` | 内置因果目标序列、连续因子、参数网格 |
| `support_resistance.py` | 支撑反弹、压力突破、突破回踩和假跌破研究函数 |
| `metrics.py` / `factor.py` | 绩效与单标的时序因子检验 |
| `api.py` / `__init__.py` | 策略运行入口与公开导出 |
| `report.py` | 单文件中文报告与原子替换 |
| `research.py` / `plots.py` | 历史批量研究、样本外评估和图表 |

策略返回与行情完全对齐的目标序列；引擎拥有成交延迟，策略不要重复 `shift`。报告消费计算结果，不决定交易行为。新增市场适配应独立处理单位、日历、公司行动，再向引擎传入标准行情。

## 本地资产如何保留

| 原路径 | 整理后的角色 | 默认提交 |
|---|---|---|
| `1min/`、`5min/`、`15min/`、`30min/`、`60min/`、`daily/` | 原位置数据湖，兼容既有脚本 | 否 |
| `ETF日线/`、`基金_复权因子/` | 第三方原始文件 | 否 |
| `data/research/` | 公司行动证据与派生数据 | 否 |
| `reports/`、`output/` | 审计结果和研究报告 | 否 |
| `notebooks/*.ipynb`、根目录 `test.ipynb` | 本地执行历史；模板可由脚本重建 | 否 |
| `build/`、`dist/`、`*.egg-info/` | 可重建产物，可能滞后于源码 | 否 |
| `.cache/`、`.runtime_deps/` | 缓存和本机依赖副本 | 否 |
| `etf_backtest/`、`tests/`、`examples/`、`scripts/` | 可审查源码 | 是 |

现有大文件不搬动、不删除。`.gitignore` 只影响未跟踪文件，不能从已有 Git 历史中删除资产。正式发布使用新构建，禁止直接分发历史 `dist/` 中的 wheel。
