#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
线上版检索 = 本地版检索？逐题比对，必须全等。

对照基准不是"我觉得对"，而是两份已经落盘的实测产物：
  1) data/index/bge/bm25.pkl   —— 本地 rank_bm25.BM25Okapi 对象（权威打分）
  2) 05_评估/结果_原始.json     —— 15 题在 bm25 模式下归档的 top-8 召回块

比对两件事：
  A. 纯 Python BM25 的逐文档分数 vs rank_bm25 的分数（数值误差 < 1e-4）
  B. 15 题 top-8 召回块的签名（#名次 公司 P页码 文/表）逐条相等

只要有一项不等，线上版就不能发——说明我写的公式和离线索引跑偏了。

用法： PYTHONPATH=tmp/site python3 10_Vercel/自检_与本地一致.py
"""
from __future__ import annotations

import json
import pickle
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tmp" / "site"))
sys.path.insert(0, str(ROOT / "03_索引"))
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT / "05_评估"))

import retriever as R  # noqa: E402

t0 = time.time()
rt = R.Retriever.load()
t_load = time.time() - t0
print(f"加载线上数据 {t_load:.2f}s｜{len(rt.chunks)} 块｜{len(rt.stats.get('companies', []))} 家")

# ---------------------------------------------------------------- A 数值比对
print("\n[A] 纯 Python BM25 vs rank_bm25.BM25Okapi —— 逐文档分数")
with open(ROOT / "data" / "index" / "bge" / "bm25.pkl", "rb") as f:
    ref = pickle.load(f)

PROBES = [
    "贵州茅台2025年营业收入是多少", "五粮液酒类产品的毛利率是多少",
    "山西汾酒2025年营业收入", "洋河股份经销商数量", "今世缘营业收入",
    "口子窖毛利率", "老白干酒2025年净利润", "哪家公司毛利率最高",
]
worst = 0.0
for q in PROBES:
    toks = R.tokenize(q)
    mine = rt.bm25.get_scores(toks)
    ref_scores = ref.get_scores(toks)
    # 参照是全量表；比对所有非零分文档 + 抽查若干零分文档
    idxs = list(mine.keys())
    idxs += [i for i in range(0, len(rt.chunks), 97)]
    for i in idxs:
        worst = max(worst, abs(mine.get(i, 0.0) - float(ref_scores[i])))
print(f"  最大绝对误差 {worst:.2e}（{len(PROBES)} 条 query，含零分抽查）")
print(f"  {'✓ 一致' if worst < 1e-4 else '✗ 不一致，线上公式与离线跑偏了'}")

# ---------------------------------------------------------------- B 逐题比对
print("\n[B] 15 题 top-8 召回块 —— 线上 vs 评估归档")
raw = json.loads((ROOT / "05_评估" / "结果_原始.json").read_text(encoding="utf-8"))


def sig(hits) -> list[str]:
    return [f"#{h['rank']} {h['chunk']['name']} P{h['chunk']['page']}"
            f"{'表' if h['chunk']['kind'] == 'table' else '文'}" for h in hits]


ok = 0
for r in raw:
    q = r["q"]
    got = sig(rt.search(q, topk=8))
    want = r["modes"]["bm25"]["hits"]
    same = got == want
    ok += same
    print(f"  Q{r['id']:>2} {'✓' if same else '✗'} {q[:26]}")
    if not same:
        print("      线上:", got[:3])
        print("      归档:", want[:3])
print(f"  {ok}/{len(raw)} 题完全一致")

# ---------------------------------------------------------------- C 冷启动耗时
print("\n[C] 单次检索耗时（冷启动之后的稳态）")
for q in PROBES[:3]:
    t = time.time()
    rt.search(q, topk=8)
    print(f"  {time.time() - t:.3f}s  {q}")

allok = (worst < 1e-4) and (ok == len(raw))
print("\n结论:", "✓ 线上版与本地版等价，可以部署" if allok else "✗ 有差异，先修再部署")
sys.exit(0 if allok else 1)
