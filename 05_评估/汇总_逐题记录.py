#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
汇总逐题记录（纯检索口径）。

作业评的是检索：召回了哪些块、答对没有、错在哪。"答对"定义为
——标准答案需要的证据（哪家公司 + 哪个数字）是否进入 top-8 召回；
"错在哪"归因到具体的召回机制，而不是笼统写"检索不准"。

四种失败机制（本次实证区分出来的）：
  ① 覆盖垄断：名额被和问句最像的那一两家吃掉（Q10 极端到 top-40 仍只覆盖 2/15 家）
  ② 召回截断：top-8 最多 8 块，"15 家里谁最…"结构上覆盖不全（Q7/Q8）
  ③ 字段错位：含数字的块在该公司内部排得很靠后，语义检索偏爱"讨论收入的段落"（Q11）
  ④ 多块拼接：一个答案要 2-3 个数字，top-8 被同一章节占满（Q12）

判定不调用任何大模型。页面里的 LLM 答案生成只是可选增强。

输入： 结果_原始.json
输出： 逐题记录.csv / 逐题记录.md
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "05_评估"

raw = json.loads((OUT / "结果_原始.json").read_text(encoding="utf-8"))

# 逐题检索判定（判据与 结果_原始.json 里的召回说明一致）
RETRIEVAL_JUDGE = {
    1: ("能答对", "gold 块（主要会计数据表/利润表）召回成功"),
    2: ("能答对", "gold 块召回成功，hybrid 第 2 位"),
    3: ("能答对", "gold 块（分产品毛利率表）第 1 位"),
    4: ("能答对", "gold 块（利润分配表）第 2 位"),
    5: ("能答对", "gold 块（经销商数量表）第 5 位"),
    6: ("能答对", "gold 块（主营业务分析表）第 3 位"),
    7: ("错——召回截断", "top-8 只覆盖 6/15 家公司；正确答案山西汾酒的块没进上下文，"
                     "『15 家里谁最…』在全局 top-8 下结构上不可能答对"),
    8: ("错——召回截断", "top-8 只覆盖 5/15 家；两家亏损公司（顺鑫农业/酒鬼酒）"
                     "至少一家进不来，只能答一半"),
    9: ("碰巧能答对", "答案茅台恰好在召回子集里；但只覆盖 5/15 家就下『最高』结论，"
                    "换问法即翻车"),
    10: ("错——覆盖垄断", "点名了两家公司，top-8 却只覆盖 1/15 家：名额全被茅台吃掉，"
                      "五粮液一块都没进（top-40 也只有 2 家）——不是跨公司比较难，"
                      "是全局排序会垄断"),
    11: ("错——字段错位", "三家点名公司只覆盖 2/15 家；且含营收金额的表格块在自家排序很靠后"
                      "（今世缘排到第 47 位），因为语义检索先命中『讨论收入的段落』"),
    12: ("错——多块拼接", "要 3 个数字（营收/净利/毛利率），top-8 被同一章节占满，"
                      "只召回 1 个数字"),
    13: ("能答对", "gold 块（含 2024/2025 两年数据的主要会计数据表）第 1 位"),
    14: ("能答对", "非数字事实也召回成功（审计意见段落）第 2 位"),
    15: ("能答对", "gold 块（在职员工数量表）第 1 位"),
}

rows = []
for r in raw:
    h = r["modes"]["hybrid"]
    vm = r["modes"]["bm25"]
    vv = r["modes"]["vector"]
    judge, why = RETRIEVAL_JUDGE[r["id"]]
    rows.append({
        "题号": r["id"],
        "分组": r["group"],
        "类型": r["type"],
        "问题": r["q"],
        "标准答案（回原文核过）": r["gold_answer"],
        "召回块（hybrid top-8）": " ｜ ".join(h["hits"]),
        "gold块召回": "是" if h["gold_hit"] else "否",
        "gold位置": h["gold_pos"] if str(h["gold_pos"]) != "" else "-",
        "bm25模式": "是" if vm["gold_hit"] else "否",
        "vector模式": "是" if vv["gold_hit"] else "否",
        "答对没有": judge,
        "错在哪": why,
        "覆盖公司数": f"{h['n_companies']}/15",
    })

cols = list(rows[0].keys())
with open(OUT / "逐题记录.csv", "w", newline="", encoding="utf-8-sig") as f:
    w = csv.DictWriter(f, fieldnames=cols)
    w.writeheader()
    w.writerows(rows)

# ---- 分组汇总 ----
groups: dict[str, list[dict]] = {}
for r in rows:
    groups.setdefault(r["分组"], []).append(r)

md = ["| 题号 | 分组 | 问题 | 标准答案 | gold召回 | bm25 | vector | 答对没有 | 错在哪 |",
      "|---:|---|---|---|---|---|---|---|---|"]
for r in rows:
    md.append(f"| {r['题号']} | {r['分组']} | {r['问题']} | {r['标准答案（回原文核过）']} | "
              f"{r['gold块召回']}({r['gold位置']}) | {r['bm25模式']} | {r['vector模式']} | "
              f"**{r['答对没有']}** | {r['错在哪']} |")

md += ["", "## 分组汇总", "",
       "| 分组 | 题数 | 检索可答 | 说明 |", "|---|---:|---:|---|"]
NOTE = {
    "① 单公司·一个数字": "点名公司 + 要一个数字，检索的主场；最好第 1 位命中，出处精确到页码",
    "④ 单公司·多块拼接与跨期": "跨期（两年数据在同一张表）没问题；要拼 2-3 个数字时会漏（Q12）",
    "② 跨公司·全景（无候选集）": "top-8 最多覆盖 8 家公司，『15 家里谁最…』结构上覆盖不全",
    "③ 跨公司·受限（给了候选集）": "对照组：即使点名了公司，全局排序仍会垄断名额（Q10 只覆盖 1/15 家）",
}
for g, rs in groups.items():
    ok = sum(1 for r in rs if r["答对没有"].startswith(("能答对", "碰巧")))
    md.append(f"| {g} | {len(rs)} | {ok}/{len(rs)} | {NOTE.get(g, '')} |")

n_ok = sum(1 for r in rows if r["答对没有"].startswith(("能答对", "碰巧")))
md += ["",
       f"**总计：{len(rows)} 题，检索可答 {n_ok}/{len(rows)}**（其中 1 题是碰巧答对，"
       "只覆盖 5/15 家就下『最高』结论，不算真会）。失败的 5 题归因见上表，"
       "对照实验见 对比_召回策略.md。",
       "",
       "口径说明：判定全部基于检索——标准答案需要的证据（哪家公司 + 哪个数字）"
       "是否进入 top-8 召回；不涉及任何大模型。"]
(OUT / "逐题记录.md").write_text("\n".join(md), encoding="utf-8")

print(f"逐题记录已重写（纯检索口径）：{len(rows)} 题，检索可答 {n_ok}/{len(rows)}")
for g, rs in groups.items():
    ok = sum(1 for r in rs if r["答对没有"].startswith(("能答对", "碰巧")))
    print(f"  {g}: {ok}/{len(rs)}")
for r in rows:
    print(f"  Q{r['题号']:>2} {r['答对没有']:<8} gold={r['gold块召回']}({r['gold位置']}) "
          f"覆盖{r['覆盖公司数']}")
