# Contributing

欢迎提交问题报告、文档改进和代码贡献。请用最小可复现案例说明问题，涉及交易行为时给出可手工核算的价格、时间与金额。

## 开发环境

```bash
python -m venv .venv
source .venv/bin/activate
# Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
```

## 检查与测试

```bash
python -m ruff check .
python -m ruff format --check .
python -m unittest discover -s tests -v
python examples/zz1000_daily_demo.py
```

CI 在 Linux Python 3.10、3.12、3.14 上运行测试，并验证源码包、wheel 与安装后的示例。

## 代码规范

- 使用 UTF-8、LF、4 空格缩进；格式和导入顺序由 Ruff 管理，配置在 `pyproject.toml`。
- 公开接口添加类型注解；DataFrame 接口说明字段、索引和单位。
- 信号、成交与估值分离，不能用未来信息决定历史订单。
- 非法输入应抛出明确异常，不静默填造数据；注释说明非显然的设计原因。
- 行为修改增加回归测试；时间序列策略用前缀不变性检查因果性。
- 限定改动范围，避免将无关格式化和模型变化混在同一个 PR 中。

## 提交 PR

说明问题、改动、验证命令及兼容性影响。更新相关文档和 CHANGELOG。不要提交凭据、生成报告、虚拟环境或未经说明的数据文件。贡献按 MIT 许可提供，请确认你有权提交相关内容。

## 构建发行包

使用空的输出目录构建，检查包内容与元数据：

```bash
python -m build --outdir dist/release
python -m twine check dist/release/*.whl dist/release/*.tar.gz
```

wheel 只含库代码；源码包包含文档、测试、示例和日频样本。发布前从源码包重建 wheel，并在干净环境验证。安全问题见 [SECURITY.md](SECURITY.md)。
