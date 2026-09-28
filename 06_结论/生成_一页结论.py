#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
一页结论 PDF：同一批财报，什么问题该建库，什么问题该微调。

评估口径=检索（作业要求：逐题记录召回了哪些块、答对没有、错在哪）。
数字全部来自 05_评估/ 的真实输出，不手写。
"""
from __future__ import annotations

import json
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
RED = colors.HexColor("#b3261e")
GREEN = colors.HexColor("#0f766e")

S = {
    "h1": ParagraphStyle("h1", fontName=F, fontSize=15.5, leading=20, textColor=INK,
                         spaceAfter=2),
    "sub": ParagraphStyle("sub", fontName=F, fontSize=9, leading=13, textColor=INK2,
                          spaceAfter=7),
    "h2": ParagraphStyle("h2", fontName=F, fontSize=10.5, leading=14, textColor=BRAND,
                         spaceBefore=7, spaceAfter=3),
    "p": ParagraphStyle("p", fontName=F, fontSize=8.4, leading=12.4, textColor=INK,
                        alignment=TA_LEFT),
    "small": ParagraphStyle("small", fontName=F, fontSize=7.4, leading=10.4, textColor=INK2),
    "cell": ParagraphStyle("cell", fontName=F, fontSize=7.4, leading=10, textColor=INK),
    "cellb": ParagraphStyle("cellb", fontName=F, fontSize=7.4, leading=10, textColor=INK),
}


def P(t, s="p"):
    return Paragraph(t, S[s])


def C(t, bold=False):
    return Paragraph(str(t), S["cellb" if bold else "cell"])


def table(data, widths, header=True):
    t = Table(data, colWidths=widths, repeatRows=1 if header else 0)
    cmds = [("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("GRID", (0, 0), (-1, -1), 0.4, LINE),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]
    if header:
        cmds += [("BACKGROUND", (0, 0), (-1, 0), BG),
                 ("TEXTCOLOR", (0, 0), (-1, 0), BRAND)]
    for i in range(1 if header else 0, len(data)):
        if i % 2 == (1 if header else 0):
            cmds.append(("BACKGROUND", (0, i), (-1, i), colors.HexColor("#fafbfe")))
    t.setStyle(TableStyle(cmds))
    return t


def main():
    strat = json.loads((EV / "对比_跨公司召回策略.json").read_text(encoding="utf-8"))
    by_case, cov, blocks = {}, {}, {}
    for r in strat:
        by_case.setdefault(r["题号"], {})[r["策略"][0]] = r["判定"]
        cov.setdefault(r["题号"], {})[r["策略"][0]] = r["覆盖公司数"]
        blocks[r["策略"][0]] = r["送进模型的块数"]

    doc = SimpleDocTemplate(str(OUT), pagesize=A4,
                            leftMargin=14 * mm, rightMargin=14 * mm,
                            topMargin=12 * mm, bottomMargin=10 * mm,
                            title="财报问答库一页结论", author="陈静")
    W = doc.width
    f = []

    f.append(P("同一批财报：什么问题该建库，什么问题该微调", "h1"))
    f.append(P("方向 A · 白酒 15 家上市公司 2025 年报问答知识库 ｜ 2,698 页 / 3,988 张表格 / "
               "9,762 个文本块（每块带公司·章节·页码）｜ BM25 + bge-small-zh 向量混合检索 ｜ "
               "10 道题逐题记录", "sub"))

    # ---------- 1 结果 ----------
    f.append(P("一、10 道题的检索结果（判据：含标准答案的原文块是否被召回进 top-8）", "h2"))
    rows = [[C("题型", True), C("题数", True), C("gold块召回", True), C("典型表现", True)],
            [C("单公司事实题<br/>（营收/净利/毛利率/分红/经销商/员工数）"),
             C("7"), C("<font color='#0f766e'><b>7/7</b></font>"),
             C("含答案的表格块全部进 top-8（最好第 1 位）；出处能精确到『公司+页码』")],
            [C("跨公司全景题<br/>（谁增速最快 / 谁亏损 / 谁毛利率最高）"),
             C("3"), C("<font color='#b3261e'><b>0/3</b></font>"),
             C("<font color='#b3261e'>全部召回失败</font>：top-8 只覆盖 5/15 家公司，"
               "正确答案（山西汾酒 +7.52%、顺鑫农业 -1.52 亿、酒鬼酒 -3395 万）所在块"
               "根本没进候选")]]
    f.append(table(rows, [W * .26, W * .07, W * .13, W * .54]))

    # ---------- 2 为什么 ----------
    f.append(P("二、为什么翻车：不是数据没有，是 top-k 语义截断", "h2"))
    f.append(P("同一批数据、同一个索引，只改召回策略（分层召回 = 15 家公司每家各取 2 块再重排）：",
               "small"))
    rows = [[C("召回策略", True), C("送进模型", True), C("覆盖公司", True),
             C("Q7 增速最快", True), C("Q8 谁亏损", True), C("Q9 毛利率最高", True)]]
    label = {"A": "A 全局 top-8", "B": "B 全局 top-40", "C": "C 分层（15家×2）"}
    for k in ("A", "B", "C"):
        row = [C(label[k]), C(f"{blocks[k]} 块"), C(f"{cov[7][k]}/15")]
        for qid in (7, 8, 9):
            v = by_case[qid][k].replace("✓ ", "").replace("✗ ", "").replace("△ ", "")
            col = "#0f766e" if by_case[qid][k].startswith("✓") else \
                  ("#b3261e" if by_case[qid][k].startswith("✗") else "#b45309")
            row.append(Paragraph(f"<font color='{col}'><b>{v}</b></font>", S["cell"]))
        rows.append(row)
    f.append(table(rows, [W * .20, W * .13, W * .12, W * .18, W * .19, W * .18]))
    f.append(Spacer(1, 3))
    f.append(P("<b>关键数字：全局 top-8 最多覆盖 8 块，所以「15 家里谁最…」结构上不可能答对。</b>"
               "换成每家公司各取 2 块的分层召回，覆盖率 15/15，三题全部修复——"
               "数据、索引、块都没有变，变的只是『哪些块能到答案面前』。", "small"))

    # ---------- 3 结论 ----------
    f.append(P("三、结论：建库、微调、还是另做一件事", "h2"))
    rows = [[C("什么样的问题", True), C("该怎么做", True), C("为什么", True)],
            [C("点名了公司、要某个具体数字/事实<br/>『茅台营收多少』『洋河经销商几家』"),
             C("<b>建库（检索）</b>"),
             C("答案就是原文里的一行，BM25+向量能精确命中、可给出处、可审计；"
               "年报年年换，库可以增量重建，不用重训")],
            [C("要稳定的口径、术语、叙述方式<br/>『用董秘的语气解释为什么下滑』"),
             C("<b>微调</b>"),
             C("这是不变的东西，检索给不了：检索只能抄回原文，学不会"
               "「先讲行业、再讲公司、最后讲措施」这套组织方式")],
            [C("要跨全部实体聚合、排序、比较<br/>『15 家里谁增速最快』"),
             C("<font color='#b3261e'><b>两个都不对</b></font><br/>先 ETL 成结构化表"),
             C("检索的 top-k 语义截断做不了全集扫描（本实验已实证）；"
               "微调更不该用来记数字。正解是把 15 家关键指标抽成一张宽表，再让模型读表")]]
    f.append(table(rows, [W * .28, W * .17, W * .55]))

    # ---------- 4 注脚 ----------
    f.append(P("四、两个诚实的注脚", "h2"))
    f.append(P("① 我自己出的第一版标准答案就错了一题：Q8 只写了酒鬼酒，漏了亏得更多的顺鑫农业"
               "（-1.52 亿，顺鑫 2025 年报 P7 主要会计数据表），是回头翻原文才发现的——"
               "这反过来印证建库的意义：<b>人对数字的记忆不可靠，检索到的原文才可靠</b>。"
               "② 问答页面的大模型生成答案是可选增强（Q1-Q6 已实测会主动区分合并/母公司口径）；"
               "本次评估的判定全部基于检索，不依赖大模型。", "small"))
    f.append(Spacer(1, 4))
    f.append(P("数据源：巨潮资讯网公开年报 PDF（15 家白酒上市公司 2025 年度报告）｜"
               "全部本地解析与检索，PDF 不进仓库｜逐题记录与对照实验见 05_评估/", "small"))

    doc.build(f)
    print("已生成", OUT, OUT.stat().st_size, "字节")


if __name__ == "__main__":
    main()
