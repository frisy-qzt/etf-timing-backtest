# 数据契约

## 推荐接口：显式 bars / events

`run_strategy(..., bars=bars, events=events, frequency="daily")` 接受单标的 DataFrame。索引须为唯一递增、无时区的 `DatetimeIndex`，含义为中国交易所本地时间。日线为日期零点；分钟为窗口右端，午休不跨段。调用者提供完整交易日和窗口网格；引擎不推断外部数据遗漏的交易日。

| 字段 | 类型 / 单位 | 用途 |
|---|---|---|
| `open`, `high`, `low`, `close` | 数值，元 / 份 | 未复权成交与估值价格 |
| `volume` | 数值，份（不是手） | 成交能力判断 |
| `amount` | 数值，元（不是千元） | VWAP 与成交有效性 |
| `frequency` | 每行相同的 `daily` 或 `Nmin` | 须与入口频率一致 |
| `valid` | 布尔 | 策略及质量过滤；高层接口应显式提供 |
| `signal_open`, `signal_high`, `signal_low`, `signal_close` | 数值 | 仅使用已生效事件构造的信号视图；无事件时等于原价 |
| `mark_close` | 可选，数值 | 估值覆盖值 |
| `limit_up`, `limit_down` | 可选，元 / 份 | 已知的当时涨跌停限制；缺失不代表已模拟 |
| `suspended`, `missing_bar` | 可选，布尔 | 停牌 / 缺失窗口标记 |

缺失值不得伪造成可交易行情。低层 `run_backtest` 与高层 `run_strategy` 的检查不完全相同：尤其日频开盘撮合不依赖当日未来总量和收盘价。不要仅依靠 `valid=False` 表示日频停牌，应提供 `suspended=True`。

目标为与 bars 索引完全一致的 Series：`0` 空仓、`1` 持有、`NaN` 不更新目标。自定义因子返回连续值 Series，与行情对齐；前视收益仅用于事后检验，不能进入策略信号。

## 事件表

`events=None` 表示未提供事件，报告会披露；空 DataFrame 表示调用者确认样本没有公司行动。非空表每行必须有 `verified=True`，支持：

| kind | 必填 | 可选 |
|---|---|---|
| `split` | `ex_date`、正数 `ratio` | `id`、`suspension_date`、`rounding` |
| `dividend` | `record_date`、`ex_date`、`pay_date`、非负 `cash_per_share` | `id` |

分红满足 `record_date < ex_date <= pay_date`。折算 `rounding="ceil"` 向上取整，其余值当前按向下取整处理；接入数据时应显式确认基金规则。`verified` 是调用者的声明，不是引擎自动验证公告来源。

## 原工作区适配器

`data_root=` 分支固定读取 `512100.SH`，需要：

```text
DATA_ROOT/
  daily/ETF_daily.parquet
  1min/ETF_1min.parquet       # 仅分钟频率需要
  data/research/corporate_actions.csv
```

源 Parquet 需要 `datetime`、`code`、OHLC、`volume`、`amount` 等字段。适配器将源成交量“手”乘 100；日线成交额“千元”乘 1000，分钟额已经为元。这是原数据集的约定，不能对任意供应商照搬。

源事件 CSV 使用 `event_id`、`event_type`、`effective_date`、`share_ratio`、`rounding` 及上述分红字段；适配器将 `consolidation` 映射为 `split`，`cash_dividend` 映射为 `dividend`。与 `data_root` 一起传自定义 events 会被拒绝；需要自定义事件时使用 bars 接口。

数据湖 `qfq/hfq` 缺日期因子设 1 是历史处理规则；研究接口的因果事件因子为另一套口径，两者不可混用。完整背景见 [历史工作区快照](local-research-history.md)。
