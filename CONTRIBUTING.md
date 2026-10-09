# 参与贡献

欢迎中文或英文 Issue / PR。请聚焦可复现问题与具体改进，尊重贡献者，不把研究结果包装为收益承诺。

## 开发环境

```bash
python -m venv .venv
source .venv/bin/activate
# Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
python -m ruff check .
python -m ruff format --check .
python -m unittest discover -s tests -v
python examples/synthetic_demo.py
```

Notebook 开发另装 `python -m pip install -e ".[dev,notebook]"`。依赖仅在 `pyproject.toml` 维护；`requirements-backtest.txt` 是兼容入口。详见 [代码规范](docs/code-style.md)。

## 提交改动

1. 基于明确问题开分支，避免把格式化、模型口径和新功能混在一个难以审查的改动中。
2. 为行为变化补充测试；写明输入数据单位、执行时点和预期账本。
3. 更新受影响的 API 文档及 `CHANGELOG.md`。
4. 运行上述检查；涉及打包时额外执行 `python -m build` 和 `python -m twine check dist/*`。
5. 在 PR 中说明问题、修复、验证结果及剩余限制。

除版本内明确命名的 `examples/data/zz1000/` 样本外，不要提交行情数据、公告 PDF、报告输出、缓存、执行后的私人 Notebook、凭据或 `.env`。提交前检查暂存区；`.gitignore` 无法保护已跟踪文件。问题复现应使用最小合成样本，不粘贴受限数据。

## 审查标准

模型变更重点检查前视偏差、交易时间、T+1、公司行动、现金与份额守恒、成本和缺失处理。尽量增加能独立手算的反例，不用仅重述实现的测试。复杂改动应由独立审查者寻找反例后再合并。

贡献代码按本项目 MIT 许可提供；请确保你有权提交。报告安全问题请读 [SECURITY.md](SECURITY.md)。
