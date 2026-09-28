#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
15 道题的逐题检索评估（判定不依赖任何大模型）。

每题记录：
  1) 三种检索模式（hybrid / bm25 / vector）各召回了哪些块
  2) gold 要素是否进 top-8（自动判，不靠感觉）
  3) 覆盖了几家公司（跨公司题的关键数字）

题目设计（15 题分四组，是为了把"失败"定位到具体机制，而不是笼统说"检索不行"）：
  第 1 组  Q1-Q6    单公司事实题：点名公司 + 要一个具体数字
  第 2 组  Q7-Q9    跨公司·全景题：没给候选集，"15 家里谁最…"
  第 3 组  Q10-Q11  跨公司·受限题：点名 2-3 家再让比较 —— 对照组！
                    若这组能答对而第 2 组不能，就证明翻车在"全集扫描"，
                    不在"跨公司比较"本身
  第 4 组  Q12-Q15  单公司·多块拼接 / 跨期 / 非数字：检验"要拼好几块"时还灵不灵

标准答案全部来自年报原文，用 查证_标准答案.py 逐条核过，不靠印象。

用法： python 05_评估/评估_15题.py              # 只跑检索（默认，不需要 key）
       RUN_LLM=1 python 05_评估/评估_15题.py     # 额外调大模型生成答案（可选增强）

输出： 05_评估/结果_原始.json、05_评估/逐题记录_原始.csv
       （人读的 逐题记录.csv/.md 由 汇总_逐题记录.py 生成）
"""
from __future__ import annotations

import csv
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "03_索引"))
sys.path.insert(0, str(ROOT / "04_问答"))

from 检索 import Index  # noqa: E402

OUT = ROOT / "05_评估"
RUN_LLM = os.environ.get("RUN_LLM") == "1"

# ----------------------------------------------------------------- 题目定义
# gold 判据四种写法：
#   {"code": 公司代码, "any": [任一子串命中即算召到]}
#   {"code": ..., "any": [...], "min_hits": N}  同一家公司多个数字，至少命中 N 个
#   {"all": [{code, any}, ...]}                 多家公司/多个数字必须全部命中
#   {"min_companies": N}                        跨公司全景题：前 8 块须覆盖 N 家公司
QUESTIONS = [
    # ---------------- 第 1 组：单公司事实题（点名公司 + 一个数字）----------------
    dict(id=1, group="① 单公司·一个数字", type="单公司·精确数字",
         q="贵州茅台2025年营业收入是多少？同比变化多少？",
         gold=dict(code="600519", any=["168,838,102,514.79", "172,054,171,890.91"]),
         answer="营业收入 1,688.38 亿元（-1.21%）；营业总收入 1,720.54 亿元（含利息收入 32.16 亿）"),

    dict(id=2, group="① 单公司·一个数字", type="单公司·利润数字",
         q="五粮液2025年归属于上市公司股东的净利润是多少？同比下降多少？",
         gold=dict(code="000858", any=["89.54亿", "89.54 亿", "8,954,257,202.51"]),
         answer="归母净利润 89.54 亿元，同比下降 71.89%"),

    dict(id=3, group="① 单公司·一个数字", type="单公司·比率",
         q="五粮液酒类产品的毛利率是多少？",
         gold=dict(code="000858", any=["83.75%"]),
         answer="酒类产品毛利率 83.75%（营收 371.04 亿 / 成本 60.28 亿）"),

    dict(id=4, group="① 单公司·一个数字", type="单公司·分红",
         q="贵州茅台2025年度的利润分配方案是什么？每10股派多少？",
         gold=dict(code="600519", any=["279.93"]),
         answer="每10股派息 279.93 元（含税）；现金分红 350.33 亿元，占归母净利 42.56%"),

    dict(id=5, group="① 单公司·一个数字", type="单公司·渠道结构",
         q="洋河股份2025年末经销商数量是多少？比上年变化多少？",
         gold=dict(code="002304", any=["8,371", "8371"]),
         answer="总计 8,371 家（省内 2,933 + 省外 5,438），报告期内净减少 495 家"),

    dict(id=6, group="① 单公司·一个数字", type="单公司·区域/增长",
         q="山西汾酒2025年营业收入是多少？同比增速多少？",
         gold=dict(code="600809", any=["38,718,257,657.74", "7.52"]),
         answer="营业收入 387.18 亿元，同比 +7.52%（15 家里唯一正增长）"),

    # ---------------- 第 2 组：跨公司·全景题（没给候选集）----------------
    dict(id=7, group="② 跨公司·全景（无候选集）", type="跨公司·全景排序",
         q="这15家白酒公司里，2025年营业收入同比增长最快的是哪一家？增速是多少？",
         gold=dict(min_companies=13),
         answer="山西汾酒 +7.52%，是唯一正增长；其余 14 家全部下滑"),

    dict(id=8, group="② 跨公司·全景（无候选集）", type="跨公司·全景筛选",
         q="这15家公司里，哪家2025年归属于上市公司股东的净利润是亏损的？亏了多少？",
         gold=dict(min_companies=13),
         answer="两家亏损：顺鑫农业 -1.52 亿（P7）、酒鬼酒 -3,395 万（P8）"
                "【第一版金标准漏了顺鑫农业，回原文核对后修正】"),

    dict(id=9, group="② 跨公司·全景（无候选集）", type="跨公司·口径比较",
         q="这15家公司里，哪家公司的酒类（或主营业务）毛利率最高？",
         gold=dict(min_companies=13),
         answer="贵州茅台 91.23%（主营业务分行业·酒类，P10）"
                "【第一版金标准写成『口径不统一无法排序』，回原文核对后修正】"),

    # ---------------- 第 3 组：跨公司·受限题（给了候选集）—— 对照组 ----------------
    dict(id=10, group="③ 跨公司·受限（给了候选集）", type="跨公司·两家比较",
         q="贵州茅台和五粮液，谁的酒类（主营业务）毛利率更高？各是多少？",
         gold=dict(all=[dict(code="600519", any=["91.23"]),
                        dict(code="000858", any=["83.75"])]),
         answer="贵州茅台 91.23%（P10）> 五粮液 83.75%（P10）"),

    dict(id=11, group="③ 跨公司·受限（给了候选集）", type="跨公司·三家比较",
         q="今世缘、口子窖、老白干酒这三家公司，2025年营业收入最高的是哪家？是多少？",
         gold=dict(all=[dict(code="603369", any=["10,180,698,281.27"]),
                        dict(code="603589", any=["3,990,595,921.88"]),
                        dict(code="600559", any=["4,121,473,456.04"])]),
         answer="今世缘 101.81 亿（10,180,698,281.27）> 老白干 41.21 亿 > 口子窖 39.91 亿"),

    # ---------------- 第 4 组：单公司但要拼好几块 / 跨期 / 非数字 ----------------
    dict(id=12, group="④ 单公司·要拼多块", type="单公司·多块拼接",
         q="贵州茅台2025年的营业收入、归母净利润、酒类毛利率分别是多少？",
         gold=dict(code="600519", any=["168,838,102,514.79", "82,320,067,101.68", "91.23"],
                   min_hits=2),
         answer="营收 1,688.38 亿（P6/P7）、归母净利 823.20 亿（P7）、酒类毛利率 91.23%（P10）"
                "——三个数字分散在 2-3 个块里，需要拼接"),

    dict(id=13, group="① 单公司·一个数字", type="单公司·跨期比较",
         q="五粮液2025年营业收入相比2024年下降了多少？降幅是多少？",
         gold=dict(code="000858", any=["-54.55%", "54.55"]),
         answer="营收 405.29 亿 vs 2024 年 891.75 亿，同比 -54.55%（P7 主要会计数据表）"),

    dict(id=14, group="① 单公司·一个数字", type="单公司·非数字事实",
         q="贵州茅台2025年度财务报表被出具了什么类型的审计意见？",
         gold=dict(code="600519", any=["标准无保留意见", "标准的无保留意见"]),
         answer="标准无保留意见（P2 重要提示 / P53 第八节·审计意见）"),

    dict(id=15, group="① 单公司·一个数字", type="单公司·非财务",
         q="贵州茅台2025年末在职员工总人数是多少？",
         gold=dict(code="600519", any=["在职员工的数量", "在职员工的数量合计", "员工"]),
         answer="需看年报「在职员工的数量」表（母公司+主要子公司）"),
]


def hits_signature(hits: list[dict]) -> list[str]:
    return [f"#{h['rank']} {h['chunk']['name']} P{h['chunk']['page']}"
            f"{'表' if h['chunk']['kind'] == 'table' else '文'}" for h in hits]


def _sub_hit(hits, sub) -> bool:
    for h in hits:
        c = h["chunk"]
        if c["code"] != sub["code"]:
            continue
        if any(k in c["text"] for k in sub["any"]):
            return True
    return False


def gold_hit(hits: list[dict], gold: dict) -> tuple[bool, object, str]:
    """gold 要素是否被召回，返回 (是否达标, 位置/命中数, 说明)。"""
    # 跨公司全景题：看覆盖了几家公司
    if "min_companies" in gold:
        n = len({h["chunk"]["code"] for h in hits})
        return (n >= gold["min_companies"], n,
                f"前 {len(hits)} 块覆盖 {n} 家公司（要求 ≥{gold['min_companies']}）")
    # 多家公司 / 多个数字必须全部命中
    if "all" in gold:
        got = [1 if _sub_hit(hits, s) else 0 for s in gold["all"]]
        n = sum(got)
        miss = [s["code"] for s, g in zip(gold["all"], got) if not g]
        return (n == len(gold["all"]), f"{n}/{len(gold['all'])}",
                "全部要素命中" if n == len(gold["all"])
                else f"缺 {len(miss)} 项要素（{','.join(miss)}）")
    # 单公司：关键数字命中几个
    code, kws = gold["code"], gold["any"]
    found = []
    for i, h in enumerate(hits, 1):
        c = h["chunk"]
        if c["code"] != code:
            continue
        for kw in kws:
            if kw in c["text"]:
                found.append((kw, i))
                break
    n = len({kw for kw, _ in found})
    need = gold.get("min_hits", 1)
    if n >= need:
        pos = min(i for _, i in found)
        return True, pos, f"命中 {n} 个关键数字（要求 ≥{need}），最早第 {pos} 块"
    has_code = any(h["chunk"]["code"] == code for h in hits)
    return False, n, (f"召到了该公司但只命中 {n}/{need} 个关键数字" if has_code
                      else f"前 {len(hits)} 块里根本没有这家公司")


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    idx = Index.load(tag="bge")
    print(f"索引 {idx.backend}｜{len(idx.chunks)} 块｜LLM={'on' if RUN_LLM else 'off'}\n")

    llm = None
    if RUN_LLM:
        import llm as _llm  # noqa: E402
        llm = _llm

    rows, raw = [], []
    for qd in QUESTIONS:
        print(f"===== Q{qd['id']} [{qd['type']}] {qd['q']}")
        rec = {"id": qd["id"], "group": qd["group"], "type": qd["type"], "q": qd["q"],
               "gold_answer": qd["answer"], "modes": {}}

        for mode in ("hybrid", "bm25", "vector"):
            h = idx.search(qd["q"], topk=8, mode=mode)
            ok, pos, why = gold_hit(h, qd["gold"])
            rec["modes"][mode] = {
                "gold_hit": ok, "gold_pos": pos, "why": why,
                "hits": hits_signature(h),
                "n_companies": len({x["chunk"]["code"] for x in h}),
                "detail": [{"name": x["chunk"]["name"], "page": x["chunk"]["page"],
                            "section": x["chunk"]["section"], "kind": x["chunk"]["kind"],
                            "by": x["by"], "text": x["chunk"]["text"][:300]} for x in h],
            }
            print(f"  [{mode:6s}] gold {'✓' if ok else '✗'} {why}")

        hits = idx.search(qd["q"], topk=8, mode="hybrid")
        g_ok, g_pos, g_why = gold_hit(hits, qd["gold"])
        rec["llm"] = {"ok": False, "model": "", "error": "本次评估只评检索，未调用大模型",
                      "answer": "", "cites": [], "ms": 0}
        if llm is not None:
            t0 = time.time()
            r = llm.answer(qd["q"], hits)
            rec["llm"] = {"ok": r["ok"], "model": r["model"], "error": r["error"],
                          "answer": r["text"], "cites": [list(x) for x in llm.cites(r["text"])],
                          "ms": int((time.time() - t0) * 1000)}
            print(f"  [LLM] {'✓' if r['ok'] else '✗'} {r['model']}")
            time.sleep(1)

        rows.append({
            "题号": qd["id"], "分组": qd["group"], "类型": qd["type"], "问题": qd["q"],
            "标准答案（回原文核过）": qd["answer"],
            "gold块召回": "是" if g_ok else "否",
            "gold位置": str(g_pos) if str(g_pos) != "" else "-",
            "召回说明": g_why,
            "bm25模式": "是" if rec["modes"]["bm25"]["gold_hit"] else "否",
            "vector模式": "是" if rec["modes"]["vector"]["gold_hit"] else "否",
            "覆盖公司数": f"{rec['modes']['hybrid']['n_companies']}/15",
            "召回块（hybrid top-8）": " ｜ ".join(hits_signature(hits)),
        })
        raw.append(rec)
        print()

    (OUT / "结果_原始.json").write_text(
        json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")

    cols = list(rows[0].keys())
    with open(OUT / "逐题记录_原始.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    print("\n已写出：结果_原始.json / 逐题记录_原始.csv"
          "（人读版由 汇总_逐题记录.py 生成）")


if __name__ == "__main__":
    main()
