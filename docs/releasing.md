# 发布流程

## 发布前

- 核对 MIT 许可与贡献权利；确认公开内容仅包含明确命名的日频样本，不包含其余行情、公告附件、凭据和私人输出。
- 更新版本与 CHANGELOG，保证文档对应当前代码。`pyproject.toml` 是发行版本来源。
- 启用 GitHub Actions、默认分支保护和私密漏洞报告；要求 CI 成功后合并。
- 仓库为 `https://github.com/frisy-qzt/etf-timing-backtest`。PyPI 上传是独立步骤，不声称已在 PyPI 发布。

## 本地验收

```bash
python -m pip install -e ".[dev]"
python -m ruff check .
python -m ruff format --check .
python -m unittest discover -s tests -v
python examples/synthetic_demo.py
python -m build --outdir .cache/release-dist
python -m twine check .cache/release-dist/*
```

使用空的输出目录或确认目录只含本次版本，避免混入原工作区旧 wheel。检查 sdist / wheel 清单，只包含代码、测试、示例和文档，只允许本版本明确命名的 `examples/data/zz1000/` 样本，不含其余数据湖及报告。sdist 可包含历史 Markdown 背景，但里面的私有资产不随包提供。

在另一个干净虚拟环境安装本次 wheel，切换到源码目录之外，确认导入来自安装目录，并用合成示例生成报告。CI 的 packaging job 做同类验证；发布前还应从 sdist 重建 wheel。

## 首次 GitHub 发布

本地整理不等于远端发布。确认文件清单后可初始化 Git、添加自己的远端并推送。先检查 `.gitignore` 是否覆盖本地资产；不要盲目上传整个文件夹。仓库公开后核对 CI 结果和首页链接，再建立带版本号的 Release。

PyPI 上传、GitHub Release 和仓库公开是单独发布步骤；当前工作流只构建并校验，不自动上传包。请使用你控制的账号和凭据管理机制，不在文件中保存 token。
