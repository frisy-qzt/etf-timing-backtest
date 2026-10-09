# 示例

在项目根目录安装 `python -m pip install -e .`。

| 示例 | 输入 | 命令 |
|---|---|---|
| `zz1000_daily_demo.py` | 仓库附带的 512100.SH 日频与公司行动 | `python examples/zz1000_daily_demo.py` |
| `synthetic_demo.py` | 固定随机种子的虚构行情 | `python examples/synthetic_demo.py` |
| `new_strategy.py` | 自行准备的完整真实数据工作区 | `python examples/new_strategy.py --data-root /path/to/data --frequency 5min` |

日频 demo 支持 `--start`、`--end`、`--fast`、`--slow`、`--output-root`、`--data-dir`，无需下载行情。首次运行会验证两份样本文件的 SHA-256。调整参数或时段后，用不同输出目录保存比较结果。

真实样本的单位、来源和权利说明见 [样本说明](data/zz1000/README.md)。合成示例的工作日只是虚构日历近似。
