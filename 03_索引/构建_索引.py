#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
构建混合索引（BM25 + 稠密向量）。

用法：
    python 03_索引/构建_索引.py                 # auto：有 sbert 就用 bge-small-zh，否则 TF-IDF+SVD
    python 03_索引/构建_索引.py --backend tfidf  # 强制用 TF-IDF+SVD（秒级，零依赖）
    python 03_索引/构建_索引.py --backend sbert  # 强制用神经 embedding（首次会下载模型）
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from 检索 import Index, ROOT  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", default="auto", choices=["auto", "sbert", "tfidf"])
    ap.add_argument("--tag", default=None, help="索引子目录名，默认按后端自动命名（bge / tfidf）")
    args = ap.parse_args()

    chunks = [json.loads(l) for l in
              (ROOT / "data" / "chunks.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    print(f"载入 {len(chunks)} 个块")
    t0 = time.time()
    idx = Index(chunks, backend=args.backend, tag=args.tag)
    idx.save()
    print(f"索引已保存（tag={idx.tag}，后端 {idx.backend}，用时 {time.time()-t0:.1f}s）→ data/index/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
