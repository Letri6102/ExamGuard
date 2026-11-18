"""Sinh các file .ipynb (chạy trên Google Colab) từ nguồn dạng "percent" trong notebooks/src/*.py.

    python notebooks/build_notebooks.py

Mỗi notebook được nhúng sẵn shared/feature_schema.json và notebooks/eg_common.py (qua %%writefile),
nên mở trên Colab là chạy được, không cần clone repo.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCHEMA = (HERE.parent / "shared" / "feature_schema.json").read_text(encoding="utf-8")
COMMON = (HERE / "eg_common.py").read_text(encoding="utf-8")


def cell(kind: str, src: str) -> dict:
    lines = src.strip("\n").splitlines(keepends=True)
    c = {"cell_type": kind, "metadata": {}, "source": lines}
    if kind == "code":
        c.update(execution_count=None, outputs=[])
    return c


def parse(path: Path) -> list[dict]:
    text = path.read_text(encoding="utf-8")
    cells = []
    for block in re.split(r"^# %%", text, flags=re.M)[1:]:
        header, _, body = block.partition("\n")
        if header.strip().startswith("[markdown]"):
            md = "\n".join(l[2:] if l.startswith("# ") else l.lstrip("#") for l in body.strip("\n").splitlines())
            cells.append(cell("markdown", md))
        elif body.strip():
            cells.append(cell("code", body))
    return cells


def build(src: Path) -> Path:
    cells = parse(src)
    shared = [
        cell("markdown", "### Mã dùng chung\nHai ô dưới ghi `feature_schema.json` (hợp đồng đặc trưng với trang web) và "
                         "`eg_common.py` (công thức đặc trưng, baseline luật, mô hình hợp nhất). Không cần sửa."),
        cell("code", "%%writefile feature_schema.json\n" + SCHEMA),
        cell("code", "%%writefile eg_common.py\n" + COMMON),
    ]
    # chèn sau ô markdown đầu tiên (tiêu đề) và ô cài đặt thư viện
    insert_at = next((i + 1 for i, c in enumerate(cells) if c["cell_type"] == "code"), 1)
    cells = cells[:insert_at] + shared + cells[insert_at:]
    nb = {"cells": cells, "metadata": {"accelerator": "GPU", "colab": {"provenance": [], "gpuType": "T4"},
                                       "kernelspec": {"name": "python3", "display_name": "Python 3"},
                                       "language_info": {"name": "python"}},
          "nbformat": 4, "nbformat_minor": 5}
    out = HERE / (src.stem + ".ipynb")
    out.write_text(json.dumps(nb, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


if __name__ == "__main__":
    for src in sorted((HERE / "src").glob("*.py")):
        print("->", build(src).name)
