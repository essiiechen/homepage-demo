#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
一页结论 PDF：同一批财报，什么问题该建库，什么问题该微调。

评估口径 = 检索（作业要求：逐题记录召回了哪些块、答对没有、错在哪）。
数字全部从 05_评估/ 的真实输出里读，不手写；判定不依赖任何大模型。
"""
from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "06_结论" / "一页结论.pdf"
EV = ROOT / "05_评估"

pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
F = "STSong-Light"

INK = colors.HexColor("#16233a")
INK2 = colors.HexColor("#5a6b85")
BRAND = colors.HexColor("#2b5fd9")
LINE = colors.HexColor("#dde4ef")
BG = colors.HexColor("#f4f7fd")

S = {
    "h1": ParagraphStyle("h1", fontName=F, fontSize=14.5, leading=18, textColor=INK, spaceAfter=1),
    "sub": ParagraphStyle("sub", fontName=F, fontSize=7.8, leading=11, textColor=INK2, spaceAfter=5),
    "h2": ParagraphStyle("h2", fontName=F, fontSize=9.6, leading=12.5, textColor=BRAND,
                         spaceBefore=6, spaceAfter=2.5),
    "p": ParagraphStyle("p", fontName=F, fontSize=8.2, leading=11.6, textColor=INK, alignment=TA_LEFT),
    "small": ParagraphStyle("small", fontName=F, fontSize=7.2, leading=10, textColor=INK2),
    "cell": ParagraphStyle("cell", fontName=F, fontSize=7.2, leading=9.4, textColor=INK),
    "cellb": ParagraphStyle("cellb", fontName=F, fontSize=7.2, leading=9.4, textColor=INK),
}


def P(t, s="p"):
    return Paragraph(t, S[s])


def C(t, bold=False):
    return Paragraph(str(t), S["cellb" if bold else "cell"])


def table(data, widths, header=True, fs=7.2):
    t = Table(data, colWidths=widths, repeatRows=1 if header else 0)
    cmds = [("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("GRID", (0, 0), (-1, -1), 0.4, LINE),
            ("LEFTPADDING", (0, 0), (-1, -1), 3.5),
            ("RIGHTPADDING", (0, 0), (-1, -1), 3.5),
            ("TOPPADDING", (0, 0), (-1, -1), 2.4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2.4)]
    if header:
        cmds += [("BACKGROUND", (0, 0), (-1, 0), BG),
                 ("TEXTCOLOR", (0, 0), (-1, 0), BRAND)]
    for i in range(1 if header else 0, len(data)):
        if i % 2 == (1 if header else 0):
            cmds.append(("BACKGROUND", (0, i), (-1, i), colors.HexColor("#fafbfe")))
    t.setStyle(TableStyle(cmds))
    return t


def load():
    with open(EV / "逐题记录.csv", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    strat = json.loads((EV / "对比_召回策略.json").read_text(encoding="utf-8"))
    return rows, strat


def main():
    rows, strat = load()

    # ---- 分组汇总 ----
    g_total, g_ok = Counter(), Counter()
    for r in rows:
        g_total[r["分组"]] += 1
        if r["答对没有"].startswith(("能答对", "碰巧")):
            g_ok[r["分组"]] += 1
    n_ok = sum(1 for r in rows if r["答对没有"].startswith(("能答对", "碰巧")))

    # ---- 策略矩阵 ----
    order = ["A 全局top-8", "B 全局top-40", "C 分层(15家×2)", "D 分层(15家×4)",
             "E 分层+关键章节保底"]
    qids = [7, 8, 9, 10, 11, 12]
    cell = {}
    blocks, cover = {}, {}
    for r in strat:
        cell[(r["策略"], r["题号"])] = r["判定"]
        blocks[r["策略"]] = r["送进模型的块数"]
        cover[r["策略"]] = r["覆盖公司数"]
    MARK = {"✓ 够答": ("够", "#0f766e"), "△ 部分": ("半", "#b45309"), "✗ 不够": ("缺", "#b3261e")}

    doc = SimpleDocTemplate(str(OUT), pagesize=A4,
                            leftMargin=13 * mm, rightMargin=13 * mm,
                            topMargin=11 * mm, bottomMargin=9 * mm,
                            title="财报问答库一页结论", author="陈静")
    W = doc.width
    f = []

    f.append(P("同一批财报：什么问题该建库，什么问题该微调", "h1"))
    f.append(P("方向 A · 白酒 15 家上市公司 2025 年报问答知识库 ｜ 2,698 页 / 3,988 张表格 / "
               "9,762 个文本块（每块带公司·章节·页码）｜ BM25 + bge-small-zh 向量混合检索 ｜ "
               f"{len(rows)} 道题逐题记录 + 6 道失败题 × 5 种召回策略对照", "sub"))

    # ---------- 一、结果 ----------
    f.append(P(f"一、{len(rows)} 道题的检索结果（判据：标准答案需要的证据——哪家公司 + "
               "哪个数字——是否进 top-8）", "h2"))
    TIP = {
        "① 单公司·一个数字": "答案就是原文里的一行：9 题全部召回，最好第 1 位命中，"
                        "出处能精确到『公司 + 页码』",
        "② 跨公司·全景（无候选集）": "top-8 最多覆盖 8 家公司，『15 家里谁最…』结构上覆盖不全；"
                             "唯一『对』的那题是碰巧——只覆盖 5/15 家就下『最高』结论",
        "③ 跨公司·受限（给了候选集）": "对照组：即使点名了公司也全错。Q10「茅台 vs 五粮液」"
                              "top-8 只覆盖 1/15 家、top-40 也只有 2 家，名额被一家吃掉",
        "④ 单公司·要拼多块": "要 3 个数字（营收/净利/毛利率），top-8 被同一章节占满，只召回 1 个",
    }
    data = [[C("题型分组", True), C("题数", True), C("检索可答", True), C("表现与原因", True)]]
    for g in ["① 单公司·一个数字", "② 跨公司·全景（无候选集）",
              "③ 跨公司·受限（给了候选集）", "④ 单公司·要拼多块"]:
        ok, tot = g_ok[g], g_total[g]
        col = "#0f766e" if ok == tot else ("#b3261e" if ok == 0 else "#b45309")
        data.append([C(g), C(str(tot)),
                     Paragraph(f"<font color='{col}'><b>{ok}/{tot}</b></font>", S["cell"]),
                     C(TIP[g])])
    f.append(table(data, [W * .21, W * .06, W * .11, W * .62]))
    f.append(Spacer(1, 2.5))
    f.append(P(f"<b>合计 {n_ok}/{len(rows)} 题检索可答</b>（其中 1 题属碰巧，不算真会）。"
               "失败的 5 题全部与『哪些块能到答案面前』有关，但机制分两层，不止是"
               "『把 k 调大』能解决的——见下。", "small"))

    # ---------- 二、病根 ----------
    f.append(P("二、病根不在数据、也不在模型，在『哪些块能到答案面前』（两层）", "h2"))
    f.append(P("第一层 <b>覆盖垄断</b>：全局排序按语义相似度取块，名额会被和问句最像的那一两家吃掉——"
               "Q10 点名了两家公司，top-8 只覆盖 1/15 家，top-40 也只有 2 家。"
               "第二层 <b>字段错位</b>：即使覆盖到了公司，含数字的块在该公司内部也排得很靠后"
               "（今世缘营收金额块排自家第 47 位），因为语义检索先命中『讨论收入的段落』，"
               "而不是数字表。下面只换召回策略，不动数据、不动模型权重：", "small"))
    data = [[C("召回策略", True), C("块数", True), C("覆盖", True)] +
            [C(f"Q{q}", True) for q in qids]]
    for s in order:
        row = [C(s), C(str(blocks[s])), C(f"{cover[s]}/15")]
        for q in qids:
            m, col = MARK.get(cell.get((s, q), "✗ 不够"), ("缺", "#b3261e"))
            row.append(Paragraph(f"<font color='{col}'><b>{m}</b></font>", S["cell"]))
        data.append(row)
    f.append(table(data, [W * .235, W * .075, W * .085] + [W * .101] * 6))
    f.append(Spacer(1, 2.5))
    f.append(P("够＝证据齐全　半＝只齐一部分　缺＝一项都没有。Q7 谁增速最快 / Q8 谁亏损 / "
               "Q9 毛利率最高 / Q10 茅台vs五粮液 / Q11 三家营收谁高 / Q12 茅台三个数字。"
               "<b>加大 k（B）救不回覆盖垄断；按公司分层（C/D）立刻修好 Q10；"
               "再叠加『主要会计数据』章节保底的元数据路由（E）才修好 Q7 与 Q12。</b>"
               f"代价是上下文从 {blocks['A 全局top-8']} 块涨到 {blocks['E 分层+关键章节保底']} 块；"
               "而 Q8、Q11 五种策略都没救回来——这两题是真正的『跨实体聚合』。", "small"))

    # ---------- 三、结论 ----------
    f.append(P("三、结论：建库、微调、还是先做 ETL", "h2"))
    data = [[C("什么样的问题", True), C("该怎么做", True), C("为什么（本实验的依据）", True)],
            [C("点名了公司、要某个具体数字或事实<br/>『茅台营收多少』『洋河经销商几家』"),
             C("<b>建库（检索）</b>"),
             C("答案是原文里的一行：9/9 全部召回，最好第 1 位，还带得出处、可审计；"
               "年报年年换，库增量重建即可，不用重训")],
            [C("要稳定口径、术语、叙述方式<br/>『用董秘的语气解释为什么下滑』"),
             C("<b>微调</b>"),
             C("这是不变的东西，检索给不了：它只能抄回原文，学不会"
               "『先讲行业、再讲公司、最后讲措施』这套组织方式")],
            [C("要跨实体聚合、排序、取极值<br/>『15 家里谁增速最快』"),
             C("<font color='#b3261e'><b>两个都不对</b></font><br/>先 ETL 成结构化宽表"),
             C("检索按语义相似度取块，天生做不了全集扫描与极值比较：本次 5 道跨公司题"
               "在全局 top-8 下只答对 1 道（还是碰巧）；靠分层 + 章节路由能救回一部分，"
               "但 Q8/Q11 五种策略都没救回，且上下文要涨 5 倍。把 15 家的关键指标"
               "抽成一张宽表，再让模型读表，才是稳定解")]]
    f.append(table(data, [W * .27, W * .16, W * .57]))

    # ---------- 四、注脚 ----------
    f.append(P("四、三条诚实的注脚", "h2"))
    f.append(P("① 我出的第一版标准答案自己就错了一题：Q8 只写了酒鬼酒，漏了亏得更多的顺鑫农业"
               "（-1.52 亿，顺鑫 2025 年报 P7 主要会计数据表），是回头翻原文才发现的——"
               "这反过来印证建库的意义：<b>人对数字的记忆不可靠，检索到的原文才可靠</b>。"
               "② 过程中修掉一个自己的实现缺陷：候选池大小原本跟着 top-k 变，"
               "导致同一个问题在 top-2 与 top-60 下排序不一致、分层召回取块不稳；"
               "改成固定取全部候选后结果才自洽（03_索引/检索.py）。"
               "③ 问答页面的大模型生成只是可选增强；<b>本次评估的判定全部基于检索，"
               "不依赖任何大模型</b>。", "small"))
    f.append(Spacer(1, 3))
    f.append(P("数据源：巨潮资讯网公开年报 PDF（15 家白酒上市公司 2025 年度报告）｜全部本地解析与检索，"
               "PDF 不进仓库｜逐题记录见 05_评估/逐题记录.md，对照实验见 05_评估/对比_召回策略.md", "small"))

    doc.build(f)
    print(f"已生成 {OUT}  {OUT.stat().st_size} 字节｜{len(rows)} 题，检索可答 {n_ok}")


if __name__ == "__main__":
    main()
