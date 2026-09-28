#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
组装线上问答应用的部署目录 09_部署/（可重复运行，幂等覆盖）。

为什么单独一个目录：
  仓库里 data/ 有 30MB 年报原件、12MB 解析中间产物、1.1GB 本地依赖（tmp/），
  这些都不该传上公网。问答应用真正需要的只有：应用代码 + 切块 + 索引。
  单独组装一个 94MB 的目录，部署时只传它。

带两套索引（bge 28M + tfidf 47M）不是浪费：bge 查询时要从 HuggingFace 拉模型，
线上若拉不到，app.py 会自动降级到零依赖的 TF-IDF+SVD 后端（见 05_评估/对照_双后端.md）。
tfidf 那 47MB 就是这条降级路径的保险。

用法： python 构建_部署目录.py
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DST = ROOT / "09_部署"

CODE_DIRS = ["03_索引", "04_问答"]
DATA_FILES = ["data/chunks.jsonl", "data/manifest.json",
              "data/切块统计.json", "data/解析统计.json"]
INDEX_TAGS = ["bge", "tfidf"]
EXTRA_FILES = ["requirements.deploy.txt", "README.md"]


def main():
    if DST.exists():
        shutil.rmtree(DST)
    (DST / "data" / "index").mkdir(parents=True)

    for d in CODE_DIRS:
        src = ROOT / d
        if not src.exists():
            sys.exit(f"缺少代码目录：{src}")
        shutil.copytree(src, DST / d, ignore=shutil.ignore_patterns("__pycache__"))

    for rel in DATA_FILES:
        src = ROOT / rel
        if not src.exists():
            sys.exit(f"缺少数据文件：{src}（先跑 02_解析 与 03_索引）")
        shutil.copy2(src, DST / "data" / Path(rel).name)

    for rel in EXTRA_FILES:
        src = ROOT / rel
        if src.exists():
            shutil.copy2(src, DST / Path(rel).name)

    for tag in INDEX_TAGS:
        src = ROOT / "data" / "index" / tag
        if not (src / "meta.json").exists():
            print(f"[警告] 索引不存在，跳过：{src}")
            continue
        shutil.copytree(src, DST / "data" / "index" / tag)

    total = sum(f.stat().st_size for f in DST.rglob("*") if f.is_file())
    n = sum(1 for f in DST.rglob("*") if f.is_file())
    print(f"已组装 {DST}")
    print(f"  {n} 个文件，{total/1024/1024:.0f} MB")
    print("  本地试跑：cd 09_部署 && PORT=8899 HOST=0.0.0.0 DISABLE_LLM=1 python 04_问答/app.py")


if __name__ == "__main__":
    main()
