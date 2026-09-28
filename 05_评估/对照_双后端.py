#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
两套向量后端的对照（判定不依赖大模型）。

为什么要有这个脚本：评估用的是 bge 神经 embedding，但**线上部署装不动 torch**
（sentence-transformers 会拖进 GB 级依赖）。所以必须回答一个问题：
换成零依赖的 TF-IDF+SVD 后端，那 15 道题的结论还成不成立？

做法：同一批切块、同一套 BM25/RRF/先验权重，只换稠密向量后端，
用 评估_15题.py 里完全相同的 gold 判据重跑一遍。

用法： python 05_评估/对照_双后端.py
输出： 05_评估/对照_双后端.md
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "03_索引"))

from 检索 import Index  # noqa: E402

OUT = ROOT / "05_评估"


def _load_eval_module():
    """按文件路径导入 评估_15题.py（文件名含中文与数字，不能直接 import）。"""
    p = ROOT / "05_评估" / "评估_15题.py"
    spec = importlib.util.spec_from_file_location("_eval15", p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def main():
    ev = _load_eval_module()
    qs = ev.QUESTIONS

    results: dict[str, list[tuple[int, str, bool, object]]] = {}
    for tag in ("bge", "tfidf"):
        d = ROOT / "data" / "index" / tag
        if not (d / "meta.json").exists():
            print(f"[跳过] 索引不存在：{d}")
            continue
        idx = Index.load(tag=tag)
        print(f"后端 {tag}: {idx.backend}｜{len(idx.chunks)} 块")
        rows = []
        for q in qs:
            hits = idx.search(q["q"], topk=8, mode="hybrid")
            ok, pos, note = ev.gold_hit(hits, q["gold"])
            n_comp = len({h["chunk"]["code"] for h in hits})
            rows.append((q["id"], q["group"], ok, pos, n_comp))
            print(f"  Q{q['id']:<2} {'✓' if ok else '✗'} 覆盖{n_comp:>2}/15  {note}")
        results[tag] = rows

    # ---------- 汇总成 md ----------
    lines = ["# 两套向量后端对照（同一批切块、同一套权重，只换稠密向量）", "",
             "| 题号 | 分组 | bge 神经 embedding | tfidf+SVD 零依赖 |",
             "|---:|---|:--:|:--:|"]
    tags = list(results)
    if len(tags) < 2:
        print("两套索引未齐全，跳过汇总")
        return
    a, b = results[tags[0]], results[tags[1]]
    for ra, rb in zip(a, b):
        lines.append(f"| {ra[0]} | {ra[1]} | {'✓' if ra[2] else '✗'} | {'✓' if rb[2] else '✗'} |")
    na, nb = sum(1 for r in a if r[2]), sum(1 for r in b if r[2])
    lines += ["", f"- **bge：{na}/{len(a)} 题检索可答**",
              f"- **tfidf+SVD：{nb}/{len(b)} 题检索可答**", "",
              "差值 = 神经 embedding 换成本地稀疏向量后丢掉的题数。"
              "若差值很小，说明结论对后端不敏感——线上用轻量后端是站得住的。", "",
              "判定口径：gold 判据与 评估_15题.py 完全相同（证据是否进 top-8），不涉及大模型。"]

    p = OUT / "对照_双后端.md"
    p.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n后端对照：bge {na}/{len(a)}　tfidf {nb}/{len(b)}")
    print(f"已生成 {p}")


if __name__ == "__main__":
    main()
