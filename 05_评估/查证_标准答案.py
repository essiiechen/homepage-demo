#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
出标准答案：不靠印象，直接从解析后的年报页文本里把关键数字捞出来人工核对。
这是评估的"金标准"——后面判断系统答对没有，全靠这张表。

用法： python 05_评估/查证_标准答案.py [关键词...]
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PAGES = ROOT / "data" / "pages"
CHUNKS = ROOT / "data" / "chunks.jsonl"

NAMES = {
    "600519": "贵州茅台", "000858": "五粮液", "000568": "泸州老窖", "600809": "山西汾酒",
    "002304": "洋河股份", "000596": "古井贡酒", "603369": "今世缘", "000799": "酒鬼酒",
    "600779": "水井坊", "603198": "迎驾贡酒", "603589": "口子窖", "600559": "老白干酒",
    "002646": "天佑德酒", "603919": "金徽酒", "000860": "顺鑫农业",
}


def load_chunks() -> list[dict]:
    return [json.loads(l) for l in CHUNKS.read_text(encoding="utf-8").splitlines() if l.strip()]


def find(chunks: list[dict], code: str | None, *pats: str, limit: int = 6) -> list[dict]:
    out = []
    for c in chunks:
        if code and c["code"] != code:
            continue
        if all(re.search(p, c["text"]) for p in pats):
            out.append(c)
    return out[:limit]


def show(title: str, hits: list[dict], n: int = 260):
    print(f"\n### {title}  （命中 {len(hits)} 块）")
    for c in hits:
        print(f"-- [{c['name']} P{c['page']}] {c['section'][:14]} {c['subsection'][:20]}")
        print("   " + re.sub(r"\s+", " ", c["text"])[:n])


def main():
    cs = load_chunks()
    args = sys.argv[1:]
    if args:                                   # 临时查某个词
        for a in args:
            show(f"包含「{a}」", find(cs, None, a), 220)
        return

    # ---------- 1) 营业总收入（合并利润表 / 主要财务指标）----------
    show("营业总收入（主要财务指标口径）",
         find(cs, None, r"营业总收入", r"^(?!.*同比)")[0:0] or
         [c for c in cs if re.search(r"营业总收入.{0,40}1[0-9]{2},[0-9]{3}", c["text"])
          or re.search(r"营业收入.{0,20}\d{3},\d{3}", c["text"])][:0] or
         find(cs, None, r"营业总收入", r"同比"))

    # ---------- 2) 归母净利润 ----------
    show("归属于上市公司股东的净利润",
         find(cs, None, r"归属于上市公司股东的净利润", r"万元|亿元|,000"))

    # ---------- 3) 酒类/主营业务毛利率 ----------
    show("分产品毛利率（酒类）",
         find(cs, None, r"毛利率", r"酒类|白酒"))

    # ---------- 4) 分红方案 ----------
    show("利润分配 / 每10股派",
         find(cs, None, r"每10股|每 10 股", r"派|分配"))

    # ---------- 5) 经销商数量 ----------
    show("经销商数量",
         find(cs, None, r"经销商", r"(数量|家数|个|户)"))


if __name__ == "__main__":
    main()
