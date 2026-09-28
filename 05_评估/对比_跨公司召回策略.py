#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
跨公司全景题为什么翻车？对照实验：换召回策略，看能不能救回来。

三道题（Q7 增速最快 / Q8 谁亏损 / Q9 毛利率最高）在"全局 top-8"下全错。
我怀疑根因是 top-k 截断：语义最像问句的那几块都挤在 2-3 家公司里，
另外 12 家根本没机会进上下文。于是换成三种策略再跑一遍：

  A 全局 top-8        现状（基线）
  B 全局 top-40       单纯加大 k
  C 分层召回          15 家公司每家取 top-2，再全局重排 —— 保证每家都有代表

如果 C 能把题答对，就说明：翻车在数据"到没到得了模型眼前"，
不在模型能力、也不在库里没数据。这是"该建库还是该微调"的关键判据。
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "03_索引"))
sys.path.insert(0, str(ROOT / "04_问答"))

from 检索 import Index  # noqa: E402
import llm  # noqa: E402

OUT = ROOT / "05_评估"

# 判据（标准答案已逐条回原文核过，见 查证_标准答案.py）
#   must_all  = 全对      must_any = 部分对（找到了最主要的答案，但不完整）
# 注意：Q8 的第一版金标准我写错了——只写了酒鬼酒，漏了亏得更多的顺鑫农业。
#      回原文核 P7 主要会计数据表才发现。这条教训写进结论里。
CASES = [
    dict(id=7, q="这15家白酒公司里，2025年营业收入同比增长最快的是哪一家？增速是多少？",
         must_all=["山西汾酒", "7.52"], must_any=["山西汾酒"],
         note="正确答案：山西汾酒 +7.52%，15 家里唯一正增长"),
    dict(id=8, q="这15家公司里，哪家2025年归属于上市公司股东的净利润是亏损的？亏了多少？",
         must_all=["顺鑫农业", "酒鬼酒"], must_any=["顺鑫农业"],
         note="正确答案：两家亏损 —— 顺鑫农业 -1.52 亿（P7）、酒鬼酒 -3,395 万（P8）"),
    dict(id=9, q="这15家公司里，哪家公司的酒类（或主营业务）毛利率最高？",
         must_all=["贵州茅台", "91.23"], must_any=["贵州茅台"],
         note="正确答案：贵州茅台 91.23%（主营业务分行业·酒类，P10）"),
]


def global_topk(idx: Index, q: str, k: int) -> list[dict]:
    return idx.search(q, topk=k, mode="hybrid")


def stratified(idx: Index, q: str, per_company: int = 2, cap: int = 30) -> list[dict]:
    """分层召回：每家公司各取 top-N，再按分数重排，保证公司覆盖度。"""
    codes = sorted({c["code"] for c in idx.chunks})
    pooled = []
    for code in codes:
        pooled += idx.search(q, topk=per_company, mode="hybrid", code=code)
    pooled.sort(key=lambda h: -h["rrf"])
    seen, out = set(), []
    for h in pooled:
        key = (h["chunk"]["code"], h["chunk"]["page"], h["chunk"]["chunk_id"])
        if key in seen:
            continue
        seen.add(key)
        out.append(h)
    return out[:cap]


def judge(ans: str, must: list[str]) -> bool:
    return all(m in ans for m in must) if must else False


def verdict(ans: str, c: dict) -> str:
    if judge(ans, c["must_all"]):
        return "✓ 对"
    if judge(ans, c.get("must_any", [])):
        return "△ 部分对"
    return "✗ 错"


def main():
    idx = Index.load(tag="bge")
    print(f"索引 {idx.backend}｜{len(idx.chunks)} 块\n")
    rows = []
    for c in CASES:
        print(f"########## Q{c['id']}  {c['q']}")
        print(f"          标准：{c['note']}")
        for name, fn, k in (("A 全局top-8", lambda q: global_topk(idx, q, 8), 8),
                            ("B 全局top-40", lambda q: global_topk(idx, q, 40), 40),
                            ("C 分层(15家×2)", lambda q: stratified(idx, q), 30)):
            hits = fn(c["q"])
            n_co = len({h["chunk"]["code"] for h in hits})
            r = llm.answer(c["q"], hits)
            v = verdict(r["text"], c) if r["ok"] else "✗ 调用失败"
            rows.append({"题号": c["id"], "策略": name, "送进模型的块数": len(hits),
                         "覆盖公司数": n_co, "判定": v,
                         "答案摘要": re.sub(r"\s+", " ", r["text"])[:260]})
            print(f"  [{name:14s}] 块 {len(hits):2d}｜覆盖 {n_co:2d}/15 家｜{v}")
            print(f"      {re.sub(chr(10), ' ', r['text'])[:170]}")
            time.sleep(1)
        print()

    (OUT / "对比_跨公司召回策略.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    md = ["| 题号 | 策略 | 送进模型块数 | 覆盖公司数 | 判定 | 答案摘要 |",
          "|---:|---|---:|---:|---|---|"]
    for r in rows:
        md.append(f"| {r['题号']} | {r['策略']} | {r['送进模型的块数']} | {r['覆盖公司数']}/15 | "
                  f"{r['判定']} | {r['答案摘要'][:90]} |")
    (OUT / "对比_跨公司召回策略.md").write_text("\n".join(md), encoding="utf-8")
    print("已写出：对比_跨公司召回策略.json / .md")


if __name__ == "__main__":
    main()
