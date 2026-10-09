# 多频策略包验收记录

日期：2026-10-08。

两名独立代理分别审查交易时序／数据聚合和公共接口／输出图表。发现并修复：

1. Series 自动 reindex 会掩盖错位：改为对原 bars 索引严格校验，仅随后裁剪评估截止日。
2. 显式 config 成交模型被入口默认覆盖：改为入口显式值 > config 显式值 > 入口默认；新入口默认买 high／卖 low，旧低层默认 VWAP。
3. 分钟聚合丢失已提供的停牌、涨跌停字段：按交易日传播，并补停牌与涨停回归测试。
4. 仓位抽样可能隐藏完整持仓阶段：保留持仓／目标／可卖数量变化点、前一点和末点。

修复后两名代理复核无新增阻断项。

验证命令：

```bash
PYTHONPATH=.runtime_deps /opt/miniconda3/bin/python -m unittest discover -s tests -v
PYTHONPATH=.runtime_deps /opt/miniconda3/bin/python scripts/validate_strategy_package.py
/opt/miniconda3/bin/python -m pip wheel . --no-deps --no-build-isolation --wheel-dir dist
```

37项测试通过。六个常用频率真实源验证的运行编号、行数、成交数和耗时见 `output/factors/validation.json`，每份报告独立存档。wheel 已构建，并在 /tmp 独立安装、离开项目目录后导入验证通过。

本地源没有完整历史涨跌停价，诊断明确披露；不将未提供的交易限制声称为已模拟。自定义策略的因果性仍需开发者运行前缀不变性测试。

最终5min示例报告已通过浏览器实际渲染检查，8类图表存在，浏览器无error日志；报告索引为 output/factors/index.html。
