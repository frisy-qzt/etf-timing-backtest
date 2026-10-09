# Examples

安装项目后运行：

| 示例 | 数据 | 命令 |
|---|---|---|
| 日频双均线 | 附带的 512100.SH 日频与公司行动 | `python examples/zz1000_daily_demo.py` |
| 合成行情 | 固定随机种子的虚构价格 | `python examples/synthetic_demo.py` |

日频示例支持 `--start`、`--end`、`--fast`、`--slow`、`--output-root` 和 `--data-dir`，加载时验证样本 SHA-256。同名报告成功生成后会替换旧文件；对比实验请使用不同输出目录。

[样本说明](data/zz1000/README.md) · [自定义策略](../docs/strategy-package.md#自定义策略)
