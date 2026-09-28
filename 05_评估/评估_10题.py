#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
10 道题的逐题评估。

每题记录：
  1) 三种检索模式（hybrid / bm25 / vector）各召回了哪些块
  2) gold 块是否进前 N（自动判，不靠感觉）
  3) 大模型基于召回块生成的答案 + 引用
  4) 人工判"答对没有"和"错在哪"（判据写在 JUDGE 里，逐题写清理由）

标准答案全部来自年报原文，用 查证_标准答案.py 逐条核过，不靠印象。

用法： python 05_评估/评估_10题.py
输出： 05_评估/结果_原始.json、05_评估/逐题记录.md、05_评估/逐题记录.csv
"""
from __future__ import annotations

import csv
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

# ----------------------------------------------------------------- 题目定义
# gold: 检索判据。{"code": 公司代码, "any": [任一子串命中即算召到]} 或
#       {"min_companies": N} 用于跨公司全景题（要求前 N 块里至少出现 N 家公司）
QUESTIONS = [
    dict(id=1, type="单公司·精确数字", q="贵州茅台2025年营业收入是多少？同比变化多少？",
         gold=dict(code="600519", any=["168,838,102,514.79", "172,054,171,890.91"]),
         answer="营业收入 1,688.38 亿元（-1.21%）；营业总收入 1,720.54 亿元（含利息收入 32.16 亿）"),

    dict(id=2, type="单公司·利润数字", q="五粮液2025年归属于上市公司股东的净利润是多少？同比下降多少？",
         gold=dict(code="000858", any=["89.54亿", "89.54 亿"]),
         answer="归母净利润 89.54 亿元，同比下降 71.89%"),

    dict(id=3, type="单公司·比率", q="五粮液酒类产品的毛利率是多少？",
         gold=dict(code="000858", any=["83.75%"]),
         answer="酒类产品毛利率 83.75%（营收 371.04 亿 / 成本 60.28 亿）"),

    dict(id=4, type="单公司·分红", q="贵州茅台2025年度的利润分配方案是什么？每10股派多少？",
         gold=dict(code="600519", any=["279.93"]),
         answer="每10股派息 279.93 元（含税）；现金分红 350.33 亿元，占归母净利 42.56%"),

    dict(id=5, type="单公司·渠道结构", q="洋河股份2025年末经销商数量是多少？比上年变化多少？",
         gold=dict(code="002304", any=["8,371", "8371"]),
         answer="总计 8,371 家（省内 2,933 + 省外 5,438），报告期内净减少 495 家"),

    dict(id=6, type="单公司·区域/增长", q="山西汾酒2025年营业收入是多少？同比增速多少？",
         gold=dict(code="600809", any=["38,718,257,657.74", "7.52"]),
         answer="营业收入 387.18 亿元，同比 +7.52%（15 家里唯一正增长）"),

    dict(id=7, type="跨公司·全景排序", q="这15家白酒公司里，2025年营业收入同比增长最快的是哪一家？增速是多少？",
         gold=dict(min_companies=13),
         answer="山西汾酒 +7.52%，是唯一正增长；其余 14 家全部下滑"),

    dict(id=8, type="跨公司·全景筛选", q="这15家公司里，哪家2025年归属于上市公司股东的净利润是亏损的？亏了多少？",
         gold=dict(min_companies=13),
         answer="两家亏损：顺鑫农业 -1.52 亿（P7）、酒鬼酒 -3,395 万（P8）【第一版金标准漏了顺鑫农业，回原文核对后修正】"),

    dict(id=9, type="跨公司·口径比较", q="这15家公司里，哪家公司的酒类（或主营业务）毛利率最高？",
         gold=dict(min_companies=13),
         answer="贵州茅台 91.23%（主营业务分行业·酒类，P10）【第一版金标准写成『口径不统一无法排序』，回原文核对后修正】"),

    dict(id=10, type="单公司·非财务", q="贵州茅台2025年末在职员工总人数是多少？",
         gold=dict(code="600519", any=["在职员工的数量", "在职员工的数量合计", "员工"]),
         answer="需看年报「在职员工的数量」表（母公司+主要子公司）"),
]

# ----------------------------------------------------------------- 人工判定
# 逐题对照原文核过后的判定。格式：id -> (是否答对, 错在哪/注意什么)
JUDGE: dict[int, tuple[str, str]] = {
    1: ("对", "金额与口径都对，还主动区分合并/母公司口径；小瑕疵：说'同比材料未给出'，"
              "实际主要会计数据表里有 -1.21，靠自己算的"),
    2: ("对", "数字与降幅全对，双重出处（P7 表 + P18 管理层讨论）"),
    3: ("对", "83.75% 正确，还主动列出核心单品 89.55%、经销 79.51% 等口径，无混淆"),
    4: ("对", "279.93 元/10股、350.33 亿、42.56% 全对，另补了中期分红 239.57 元"),
    5: ("对", "8,371 家、-495 家、省内 -66 / 省外 -429 全对"),
    6: ("对", "387.18 亿与 +7.52% 都算对了；同样把'同比'当成未直接披露（实际 P9 表里有 7.52）"),
    7: ("错", "答成金徽酒 -3.40%。根因：top-8 只覆盖 5/15 家公司，山西汾酒那块根本没进上下文，"
              "模型在错误子集里做排序，还诚实提示了'材料不足'"),
    8: ("错", "答成'无一家亏损'。根因：top-8 覆盖的 5 家里恰好没有顺鑫农业和酒鬼酒；"
              "另外模型用'分红÷分红率反推净利润'是危险推断，应判未提及"),
    9: ("对（但靠运气）", "答茅台 91.23% 与原文一致；但只比较了 5 家就下'最高'结论，"
                     "茅台恰好在召回子集里，换个问法就可能翻车"),
    10: ("对", "34,992 人正确（母公司 33,504 + 子公司 1,488）；页码引用 P32，实际在 P31，"
               "偏 1 页（PDF 页与印刷页对齐问题）"),
}


def hits_signature(hits: list[dict]) -> list[str]:
    return [f"#{h['rank']} {h['chunk']['name']} P{h['chunk']['page']}"
            f"{'表' if h['chunk']['kind']=='table' else '文'}" for h in hits]


def gold_hit(hits: list[dict], gold: dict) -> tuple[bool, int | None, str]:
    """gold 块是否进前 N，返回 (是否命中, 第几位, 说明)。"""
    if "min_companies" in gold:
        codes = {h["chunk"]["code"] for h in hits}
        n = len(codes)
        return (n >= gold["min_companies"], n,
                f"前 {len(hits)} 块覆盖 {n} 家公司（要求 ≥{gold['min_companies']}）")
    code = gold["code"]
    for i, h in enumerate(hits, 1):
        c = h["chunk"]
        if c["code"] != code:
            continue
        for kw in gold["any"]:
            if kw in c["text"]:
                return True, i, f"命中「{kw}」于第 {i} 块"
    has_code = any(h["chunk"]["code"] == code for h in hits)
    return False, None, (f"召到了该公司但没有含关键数字的块" if has_code
                         else f"前 {len(hits)} 块里根本没有这家公司")


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    idx = Index.load(tag="bge")
    print(f"索引 {idx.backend}｜{len(idx.chunks)} 块\n")

    rows, raw = [], []
    for qd in QUESTIONS:
        print(f"===== Q{qd['id']} [{qd['type']}] {qd['q']}")
        rec = {"id": qd["id"], "type": qd["type"], "q": qd["q"],
               "gold_answer": qd["answer"], "modes": {}}

        # 三种检索模式各跑一遍（只对比召回，不重复烧 LLM）
        for mode in ("hybrid", "bm25", "vector"):
            h = idx.search(qd["q"], topk=8, mode=mode)
            ok, pos, why = gold_hit(h, qd["gold"])
            rec["modes"][mode] = {
                "gold_hit": ok, "gold_pos": pos, "why": why,
                "hits": hits_signature(h),
                "detail": [{"name": x["chunk"]["name"], "page": x["chunk"]["page"],
                            "section": x["chunk"]["section"], "kind": x["chunk"]["kind"],
                            "by": x["by"], "text": x["chunk"]["text"][:300]} for x in h],
            }
            print(f"  [{mode:6s}] gold {'✓' if ok else '✗'} {why}")

        # 主模式取 hybrid 的结果去问大模型
        hits = idx.search(qd["q"], topk=8, mode="hybrid")
        t0 = time.time()
        r = llm.answer(qd["q"], hits)
        dt = int((time.time() - t0) * 1000)
        rec["llm"] = {"ok": r["ok"], "model": r["model"], "error": r["error"],
                      "answer": r["text"], "cites": [list(x) for x in llm.cites(r["text"])],
                      "ms": dt}
        print(f"  [LLM] {'✓' if r['ok'] else '✗'} {r['model']} {dt}ms｜引用 {len(rec['llm']['cites'])} 处")
        if r["ok"]:
            print("  " + re.sub(r"\s+", " ", r["text"])[:400])
        print()

        g_ok, g_pos, g_why = gold_hit(hits, qd["gold"])
        judge = JUDGE.get(qd["id"], ("", ""))
        rows.append({
            "题号": qd["id"], "类型": qd["type"], "问题": qd["q"],
            "标准答案": qd["answer"],
            "gold块召回": "是" if g_ok else "否", "gold位置": g_pos or "", "召回说明": g_why,
            "bm25模式": "是" if rec["modes"]["bm25"]["gold_hit"] else "否",
            "vector模式": "是" if rec["modes"]["vector"]["gold_hit"] else "否",
            "LLM生成": "是" if r["ok"] else "否", "引用数": len(rec["llm"]["cites"]),
            "是否答对": judge[0], "错在哪": judge[1],
            "召回块": " ｜ ".join(hits_signature(hits)),
            "模型答案": re.sub(r"\s+", " ", r["text"])[:600],
        })
        raw.append(rec)
        time.sleep(1)   # 免费模型有速率限制，别打太猛

    (OUT / "结果_原始.json").write_text(
        json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")

    cols = list(rows[0].keys())
    with open(OUT / "逐题记录.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)

    # Markdown 版（给 README 和结论页用）
    md = ["| 题号 | 类型 | 问题 | 标准答案 | gold块召回 | bm25 | vector | 答对 | 错在哪 |",
          "|---:|---|---|---|---|---|---|---|---|"]
    for r in rows:
        md.append(f"| {r['题号']} | {r['类型']} | {r['问题']} | {r['标准答案']} | "
                  f"{r['gold块召回']}({r['gold位置'] or '-'}) | {r['bm25模式']} | {r['vector模式']} | "
                  f"{r['是否答对'] or '待判'} | {r['错在哪'] or ''} |")
    (OUT / "逐题记录.md").write_text("\n".join(md), encoding="utf-8")
    print("\n已写出：结果_原始.json / 逐题记录.csv / 逐题记录.md")


if __name__ == "__main__":
    main()
