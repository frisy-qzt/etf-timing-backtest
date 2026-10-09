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

## 文件读取适配器

推荐自行读取行情后通过 `bars=` 传入。`load_market_data(root, frequency)` 是一个固定格式的可选适配器，筛选 `512100.SH`，要求目录包含：

```text
DATA_ROOT/
  daily/ETF_daily.parquet
  1min/ETF_1min.parquet
  data/research/corporate_actions.csv
```

日频不需要分钟文件。源 Parquet 需要 `datetime`、`code`、OHLC、`volume`、`amount`；此适配器假定源量为手、日线额为千元、分钟额为元，并转换为标准单位。使用其他供应商数据时请自行转换，不要盲用此适配器。

源事件表字段为 `event_id`、`event_type`、`effective_date`、`share_ratio`、`rounding` 及分红日期；`consolidation` 映射为 `split`，`cash_dividend` 映射为 `dividend`。自定义事件与 `bars=` 一起传入，不与 `data_root=` 混用。

仓库附带的样本已经标准化，使用 [日频示例](../examples/README.md) 的 `load_sample()` 加载，不要再次转换单位。
