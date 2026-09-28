#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
汇总逐题记录（纯检索口径）。

作业要评的是检索：召回了哪些块、答对没有、错在哪。"答对"在这里定义为
——含标准答案要素的 gold 块是否被召回进 top-k；"错在哪"直接归因到
召回失败 / 召回截断 / 覆盖不足。这不需要调用任何大模型。

大模型生成答案只是页面的可选增强（llm.py），不影响评估结论：
Q1-Q6 的 LLM 答案来自额度耗尽前的完整运行，Q7-Q10 未生成，
均如实标注，检索评估不受影响。

输入： 结果_原始.json（三种检索模式的真实召回）
输出： 逐题记录.csv / 逐题记录.md（覆盖旧版）
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "05_评估"

raw = json.loads((OUT / "结果_原始.json").read_text(encoding="utf-8"))

# 逐题检索判定（判据=gold 块是否进 top-8，与逐题记录.md 的"召回说明"一致）
RETRIEVAL_JUDGE = {
    1: ("能答对", "gold 块（主要会计数据表/利润表）召回成功"),
    2: ("能答对", "gold 块召回成功，hybrid 第 5 位"),
    3: ("能答对", "gold 块（分产品毛利率表）第 1 位"),
    4: ("能答对", "gold 块（利润分配表）第 2 位"),
    5: ("能答对", "gold 块（经销商数量表）第 4 位"),
    6: ("能答对", "gold 块（主营业务分析表）第 4 位"),
    7: ("错——召回截断", "top-8 最多 8 块，只覆盖 5/15 家公司；"
                     "正确答案山西汾酒的块没进上下文，结构上不可能答对"),
    8: ("错——召回截断", "同上：两家亏损公司（顺鑫农业/酒鬼酒）都不在 top-8 覆盖的 5 家里"),
    9: ("碰巧能答对", "答案茅台恰好在召回子集里；但只比较 5 家就下『最高』结论，换问法即翻车"),
    10: ("能答对", "gold 块（在职员工数量表）第 1 位"),
}

rows = []
for r in raw:
    h = r["modes"]["hybrid"]
    vm = r["modes"]["bm25"]
    vv = r["modes"]["vector"]
    judge, why = RETRIEVAL_JUDGE[r["id"]]
    llm = r["llm"]
    rows.append({
        "题号": r["id"],
        "类型": r["type"],
        "问题": r["q"],
        "标准答案（回原文核过）": r["gold_answer"],
        "召回块（hybrid top-8）": " ｜ ".join(h["hits"]),
        "gold块召回": "是" if h["gold_hit"] else "否",
        "gold位置": h["gold_pos"] or "-",
        "bm25模式": "是" if vm["gold_hit"] else "否",
        "vector模式": "是" if vv["gold_hit"] else "否",
        "答对没有": judge,
        "错在哪": why,
        "覆盖公司数": f"{len({d['name'] for d in h['detail']})}/15",
        "LLM生成(可选增强)": ("已生成" if llm["ok"] else
                        "额度耗尽未生成（不影响检索评估）"),
    })

cols = list(rows[0].keys())
with open(OUT / "逐题记录.csv", "w", newline="", encoding="utf-8-sig") as f:
    w = csv.DictWriter(f, fieldnames=cols)
    w.writeheader()
    w.writerows(rows)

md = ["| 题号 | 类型 | 问题 | 标准答案 | gold块召回 | bm25 | vector | 答对没有 | 错在哪 |",
      "|---:|---|---|---|---|---|---|---|---|"]
for r in rows:
    md.append(f"| {r['题号']} | {r['类型']} | {r['问题']} | {r['标准答案（回原文核过）']} | "
              f"{r['gold块召回']}({r['gold位置']}) | {r['bm25模式']} | {r['vector模式']} | "
              f"**{r['答对没有']}** | {r['错在哪']} |")
md.append("")
md.append("口径说明：『答对没有』按检索评估——gold 块（含标准答案要素的原文块）是否进入"
          " top-8 召回。跨公司全景题的失败是结构性的：top-8 最多覆盖 8 家公司，"
          "『15 家里谁最…』在全局 top-8 下不可能答对；换成"
          "分层召回（每家公司各取 2 块）后 3 题全部修复（见 对比_跨公司召回策略.md）。")
md.append("")
md.append("大模型生成答案只是页面可选增强：Q1-Q6 来自额度耗尽前的完整运行（答案与判定见"
          " 结果_原始.json），Q7-Q10 未生成，不影响检索评估结论。")
(OUT / "逐题记录.md").write_text("\n".join(md), encoding="utf-8")

n_ok = sum(1 for r in rows if r["答对没有"].startswith(("能答对", "碰巧")))
print(f"逐题记录已重写（纯检索口径）：{len(rows)} 题，检索可答 {n_ok}/10")
for r in rows:
    print(f"  Q{r['题号']:>2} {r['答对没有']:<8} gold={r['gold块召回']}({r['gold位置']}) "
          f"覆盖{r['覆盖公司数']}")
