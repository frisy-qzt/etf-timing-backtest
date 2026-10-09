"""Execute and save all outputs, then export a self-contained notebook HTML."""

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".runtime_deps"))
for folder in [".cache/ipython", ".cache/jupyter", ".cache/matplotlib", ".cache/fontconfig"]:
    (ROOT / folder).mkdir(parents=True, exist_ok=True)
os.environ.setdefault("XDG_CACHE_HOME", str(ROOT / ".cache"))
os.environ.setdefault("IPYTHONDIR", str(ROOT / ".cache/ipython"))
os.environ.setdefault("JUPYTER_RUNTIME_DIR", str(ROOT / ".cache/jupyter"))
os.environ.setdefault("MPLCONFIGDIR", str(ROOT / ".cache/matplotlib"))
import nbformat
from nbclient import NotebookClient
from nbconvert import HTMLExporter

path = ROOT / "notebooks/中证1000ETF_双频双区间回测.ipynb"
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument(
    "--html-only",
    action="store_true",
    help="Export already executed notebook without recalculating",
)
args = parser.parse_args()
notebook = nbformat.read(path, as_version=4)


def announce(cell, cell_index, **kwargs):
    if cell.cell_type == "code":
        print(f"Executing cell {cell_index + 1}/{len(notebook.cells)}", flush=True)


client = NotebookClient(
    notebook,
    timeout=3600,
    kernel_name="python3",
    resources={"metadata": {"path": str(ROOT)}},
    on_cell_start=announce,
)
if not args.html_only:
    try:
        client.execute()
    except Exception:
        nbformat.write(notebook, path.with_suffix(".failed.ipynb"))
        raise
nbformat.validate(notebook)
nbformat.write(notebook, path)
exporter = HTMLExporter(
    raw_template="""{% extends 'lab/index.html.j2' %}
{% block html_head_js_requirejs %}{% endblock %}
{% block html_head_js_mathjax %}{% endblock %}
{% block html_head_js_mermaidjs %}{% endblock %}
"""
)
exporter.exclude_input_prompt = True
exporter.exclude_output_prompt = True
body, _ = exporter.from_notebook_node(notebook)
dest = ROOT / "output/backtest/notebook.html"
dest.write_text(body)
print(f"Saved executed notebook and {dest}", flush=True)
