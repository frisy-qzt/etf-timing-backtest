# Notebook 研究

原始 `中证1000ETF_双频双区间回测.ipynb` 及根目录 `test.ipynb` 保留为本地记录，执行结果可能含私人路径和大体积报告，默认不提交。公开快速开始使用 [合成数据示例](../examples/README.md)。

已有原始数据工作区时，可从根目录执行：

```bash
python -m pip install -e ".[notebook]"
python scripts/build_backtest_notebook.py
python scripts/execute_backtest_notebook.py
```

**构建脚本会覆盖同名 Notebook 并清除旧输出，运行前自行备份。** 执行脚本需要真实行情和公司行动文件，输出位于 `output/backtest/`。研究参数和历史范围见 [实施记录](../docs/backtest-implementation.md)。
