#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
线上版该用哪种召回策略？在 15 题上实测，不要凭感觉选。

对照三条策略（都是线上这份纯 BM25 代码跑的，不是本地 bge）：
  global      全局 top-8            —— 离线评估归档的那条基线（8/15）
  stratified  分层（每家 2 块+保底）  —— 修跨公司覆盖度，但会稀释单公司题
  auto        点名公司走全局，否则分层

gold 判据直接复用 05_评估/评估_15题.py，判定口径与正式评估完全相同，
不引入任何大模型。

用法： PYTHONPATH=tmp/site python3 10_Vercel/评估_线上策略.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tmp" / "site"))
sys.path.insert(0, str(ROOT / "03_索引"))
sys.path.insert(0, str(ROOT / "05_评估"))
sys.path.insert(0, str(ROOT / "lib"))

import retriever as R  # noqa: E402
from 评估_15题 import QUESTIONS, gold_hit  # noqa: E402

rt = R.Retriever.load()
print(f"线上数据 {len(rt.chunks)} 块\n")

STRATEGIES = {
    "global": lambda q: rt.search(q, topk=8),
    "stratified": lambda q: rt.stratified(q, per_company=2, cap=45),
    "auto": lambda q: rt.auto(q, topk=8),
}

score = {k: 0 for k in STRATEGIES}
rows = []
for qd in QUESTIONS:
    line = f"Q{qd['id']:>2}"
    for name, fn in STRATEGIES.items():
        hits = fn(qd["q"])[:8]        # 判定口径统一取前 8 块
        ok, pos, why = gold_hit(hits, qd["gold"])
        score[name] += ok
        line += f"  {name[:4]}={'✓' if ok else '✗'}"
    rows.append(line)
    print(line)

print()
for k, v in score.items():
    print(f"  {k:12s} {v}/{len(QUESTIONS)}")
best = max(score, key=lambda k: score[k])
print(f"\n最优策略：{best}（{score[best]}/{len(QUESTIONS)}）—— 线上默认就用它")
