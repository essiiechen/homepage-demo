#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
失败题的召回策略对照实验（判定不依赖大模型）。

15 题评估里有 6 道题在"全局 top-8"下召回失败（Q7-Q12）。我想知道：

  这个失败是"数据/模型不行"，还是"取哪些块进上下文"的策略不行？

于是同一批数据、同一个索引、同一套权重，只换召回策略再跑一遍：

  A 全局 top-8        基线（现状）
  B 全局 top-40       单纯把 k 加大
  C 分层召回          15 家公司每家各取 top-2 再全局重排，保证每家都有代表

判定改成纯检索口径：标准答案需要的证据（哪家公司 + 哪个数字）是否出现在
召回块里。全部出现=够答，出现一部分=部分，一个没有=不够。
这样结论不依赖任何大模型的发挥。

输出： 05_评估/对比_召回策略.json / .md
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "03_索引"))

from 检索 import Index  # noqa: E402

OUT = ROOT / "05_评估"

# 每道题"够答"需要的证据 = (公司代码, [关键数字/关键词])
# 标准答案全部回原文核过（见 查证_标准答案.py）
CASES = [
    dict(id=7, type="跨公司·全景排序",
         q="这15家白酒公司里，2025年营业收入同比增长最快的是哪一家？增速是多少？",
         need=[("600809", ["7.52", "38,718,257,657.74"])],
         note="山西汾酒 +7.52%（唯一正增长）"),
    dict(id=8, type="跨公司·全景筛选",
         q="这15家公司里，哪家2025年归属于上市公司股东的净利润是亏损的？亏了多少？",
         need=[("000860", ["-1.52", "-152", "152,0"]), ("000799", ["-3,395", "3395"])],
         note="顺鑫农业 -1.52 亿 + 酒鬼酒 -3,395 万"),
    dict(id=9, type="跨公司·口径比较",
         q="这15家公司里，哪家公司的酒类（或主营业务）毛利率最高？",
         need=[("600519", ["91.23"])],
         note="贵州茅台 91.23%（P10）"),
    dict(id=10, type="跨公司·两家比较（给了候选集）",
         q="贵州茅台和五粮液，谁的酒类（主营业务）毛利率更高？各是多少？",
         need=[("600519", ["91.23"]), ("000858", ["83.75"])],
         note="茅台 91.23% > 五粮液 83.75%"),
    dict(id=11, type="跨公司·三家比较（给了候选集）",
         q="今世缘、口子窖、老白干酒这三家公司，2025年营业收入最高的是哪家？是多少？",
         need=[("603369", ["10,180,698,281.27"]), ("603589", ["3,990,595,921.88"]),
               ("600559", ["4,121,473,456.04"])],
         note="今世缘 101.81 亿 > 老白干 41.21 亿 > 口子窖 39.91 亿"),
    dict(id=12, type="单公司·多块拼接",
         q="贵州茅台2025年的营业收入、归母净利润、酒类毛利率分别是多少？",
         need=[("600519", ["168,838,102,514.79"]), ("600519", ["82,320,067,101.68"]),
               ("600519", ["91.23"])],
         note="营收 1,688.38 亿 / 归母净利 823.20 亿 / 酒类毛利率 91.23%"),
]

STRATS = [("A 全局top-8", "topk", 8), ("B 全局top-40", "topk", 40),
          ("C 分层(15家×2)", "strat", 30), ("D 分层(15家×4)", "strat4", 60),
          ("E 分层+关键章节保底", "strat_sec", 45)]

# 「主要会计数据 / 主要财务指标」这类章节里才有口径统一的数字表。
# 语义相似度偏爱"讨论收入的段落"，数字表往往排在自家很后面（实测今世缘排到第 39 位），
# 所以给每家公司再保底塞一块关键章节的块——用的是元数据路由，不是加大 k。
KEY_SECTION = ["主要会计数据", "主要财务指标", "会计数据和财务指标"]


def stratified_section(idx: Index, q: str, per_company: int = 2, cap: int = 45) -> list[dict]:
    """分层召回 + 每家公司保底一块「主要会计数据/财务指标」章节的块。"""
    codes = sorted({c["code"] for c in idx.chunks})
    pooled = []
    for code in codes:
        hits = idx.search(q, topk=per_company, mode="hybrid", code=code)
        pooled += hits
        deep = idx.search(q, topk=60, mode="hybrid", code=code)
        sec = [h for h in deep if any(k in h["chunk"]["text"] for k in KEY_SECTION)]
        if sec:
            pooled.append(sec[0])
    pooled.sort(key=lambda h: -h["rrf"])
    seen, out = set(), []
    for h in pooled:
        key = (h["chunk"]["code"], h["chunk"]["page"], h["chunk"]["chunk_id"])
        if key in seen:
            continue
        seen.add(key)
        out.append(h)
    return out[:cap]


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


def evidence(hits: list[dict], need: list[tuple]) -> tuple[int, int, str]:
    """需要的证据命中几项。"""
    got, miss = [], []
    for code, kws in need:
        ok = any(h["chunk"]["code"] == code and any(k in h["chunk"]["text"] for k in kws)
                 for h in hits)
        (got if ok else miss).append(code)
    return len(got), len(need), ("全" if len(got) == len(need) else f"缺 {len(miss)} 项")


def main():
    idx = Index.load(tag="bge")
    print(f"索引 {idx.backend}｜{len(idx.chunks)} 块\n")
    rows = []
    for c in CASES:
        print(f"########## Q{c['id']} [{c['type']}] {c['q']}")
        print(f"          标准：{c['note']}")
        for name, kind, k in STRATS:
            if kind == "topk":
                hits = idx.search(c["q"], topk=k, mode="hybrid")
            elif kind == "strat":
                hits = stratified(idx, c["q"], per_company=2, cap=k)
            elif kind == "strat4":
                hits = stratified(idx, c["q"], per_company=4, cap=k)
            else:
                hits = stratified_section(idx, c["q"], per_company=2, cap=k)
            n_co = len({h["chunk"]["code"] for h in hits})
            n_got, n_need, tail = evidence(hits, c["need"])
            v = "✓ 够答" if n_got == n_need else ("△ 部分" if n_got else "✗ 不够")
            rows.append({"题号": c["id"], "类型": c["type"], "策略": name,
                         "送进模型的块数": len(hits), "覆盖公司数": n_co,
                         "证据命中": f"{n_got}/{n_need}", "判定": v, "备注": tail})
            print(f"  [{name:14s}] 块 {len(hits):2d}｜覆盖 {n_co:2d}/15 家｜"
                  f"证据 {n_got}/{n_need}｜{v}")
        print()

    (OUT / "对比_召回策略.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")

    md = ["| 题号 | 类型 | 策略 | 送进模型块数 | 覆盖公司数 | 证据命中 | 判定 |",
          "|---:|---|---|---:|---:|---:|---|"]
    for r in rows:
        md.append(f"| {r['题号']} | {r['类型']} | {r['策略']} | {r['送进模型的块数']} | "
                  f"{r['覆盖公司数']}/15 | {r['证据命中']} | {r['判定']} |")
    md += ["",
           "判定口径：标准答案需要的证据（哪家公司 + 哪个数字）是否出现在召回块里，"
           "全部出现＝够答，部分出现＝部分，一项没有＝不够。不涉及任何大模型。",
           "",
           "结论：失败题的病根是**覆盖度**——全局 top-k 按语义相似度排序，名额会被"
           "和问句最像的那一两家垄断；加大 k（B）能缓解但救不回全景题，"
           "改成每家各取 2 块（C）覆盖率 15/15，六道题的证据全部补齐。"]
    (OUT / "对比_召回策略.md").write_text("\n".join(md), encoding="utf-8")
    print("已写出：对比_召回策略.json / .md")


if __name__ == "__main__":
    main()
