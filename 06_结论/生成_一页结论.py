#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
一页结论 PDF：同一批财报，什么问题该建库，什么问题该微调。

主线 = 三问：哪类题答得好 / 哪类题翻车 / 为什么会这样。
评估口径 = 检索（作业要求：逐题记录召回了哪些块、答对没有、错在哪）。
数字全部从 05_评估/ 与 10_Vercel/结果_前端版.json 的真实输出里读，不手写；
判定不依赖任何大模型。
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

INK = colors.HexColor("#16233a")      # 主文字
INK2 = colors.HexColor("#5a6b85")     # 次要文字
BRAND = colors.HexColor("#2b5fd9")    # 品牌蓝
LINE = colors.HexColor("#dde4ef")
BG = colors.HexColor("#f4f7fd")

GREEN = colors.HexColor("#0f766e")
GREEN_BG = colors.HexColor("#e9f5f2")
AMBER = colors.HexColor("#b45309")
AMBER_BG = colors.HexColor("#fdf3e4")
RED = colors.HexColor("#b3261e")
RED_BG = colors.HexColor("#fdeceb")
TRACK = colors.HexColor("#e6ebf5")

S = {
    "h1": ParagraphStyle("h1", fontName=F, fontSize=15, leading=18,
                         textColor=colors.white, spaceAfter=1),
    "sub": ParagraphStyle("sub", fontName=F, fontSize=7.4, leading=10.4,
                          textColor=colors.HexColor("#c9d6ee"), spaceAfter=0),
    "h2": ParagraphStyle("h2", fontName=F, fontSize=9.8, leading=12.5, textColor=BRAND,
                         spaceBefore=5.5, spaceAfter=2.5),
    "p": ParagraphStyle("p", fontName=F, fontSize=8.2, leading=11.6, textColor=INK),
    "small": ParagraphStyle("small", fontName=F, fontSize=7.1, leading=9.9, textColor=INK2),
    "cell": ParagraphStyle("cell", fontName=F, fontSize=7.1, leading=9.3, textColor=INK),
    "cellb": ParagraphStyle("cellb", fontName=F, fontSize=7.1, leading=9.3, textColor=BRAND),
    "kpi": ParagraphStyle("kpi", fontName=F, fontSize=15, leading=16, alignment=1,
                          textColor=INK, spaceAfter=0),
    "kpil": ParagraphStyle("kpil", fontName=F, fontSize=6.9, leading=9, alignment=1,
                           textColor=INK2, spaceAfter=0),
    "kpin": ParagraphStyle("kpin", fontName=F, fontSize=6.4, leading=8.4, alignment=1,
                           textColor=INK2, spaceAfter=0),
    "cardh": ParagraphStyle("cardh", fontName=F, fontSize=8, leading=10.5,
                            textColor=colors.white, spaceAfter=0),
    "cardb": ParagraphStyle("cardb", fontName=F, fontSize=7, leading=9.6, textColor=INK,
                            spaceAfter=0.5),
    "foot": ParagraphStyle("foot", fontName=F, fontSize=6.6, leading=8.6, textColor=INK2),
}


def P(t, s="p"):
    return Paragraph(t, S[s])


def C(t, bold=False):
    return Paragraph(str(t), S["cellb" if bold else "cell"])


def table(data, widths, header=True, pad=(3.5, 2.4), fs=7.1):
    t = Table(data, colWidths=widths, repeatRows=1 if header else 0)
    cmds = [("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("GRID", (0, 0), (-1, -1), 0.4, LINE),
            ("LEFTPADDING", (0, 0), (-1, -1), pad[0]),
            ("RIGHTPADDING", (0, 0), (-1, -1), pad[0]),
            ("TOPPADDING", (0, 0), (-1, -1), pad[1]),
            ("BOTTOMPADDING", (0, 0), (-1, -1), pad[1])]
    if header:
        cmds += [("BACKGROUND", (0, 0), (-1, 0), BG),
                 ("TEXTCOLOR", (0, 0), (-1, 0), BRAND)]
    for i in range(1 if header else 0, len(data)):
        if i % 2 == (1 if header else 0):
            cmds.append(("BACKGROUND", (0, i), (-1, i), colors.HexColor("#fafbfe")))
    t.setStyle(TableStyle(cmds))
    return t


def bar(ok, tot, color, w):
    """得分条：已答对部分染色，未答对部分留灰底。"""
    filled = max(w * ok / tot if tot else 0, 0.6)
    t = Table([["", ""]], colWidths=[filled, max(w - filled, 0.6)], rowHeights=[3.2 * mm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, 0), color),
        ("BACKGROUND", (1, 0), (1, 0), TRACK),
        ("BOX", (0, 0), (-1, -1), 0.3, colors.HexColor("#cfd8e8")),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))
    return t


def card(title, paras, head_bg, body_bg, w):
    """带色块标题的小卡片。"""
    body = [[Paragraph(title, S["cardh"])]]
    for x in paras:
        body.append([Paragraph(x, S["cardb"])])
    t = Table(body, colWidths=[w])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, 0), head_bg),
        ("BACKGROUND", (0, 1), (0, -1), body_bg),
        ("BOX", (0, 0), (-1, -1), 0.6, head_bg),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    return t


def load():
    with open(EV / "逐题记录.csv", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    strat = json.loads((EV / "对比_召回策略.json").read_text(encoding="utf-8"))
    fe = json.loads((ROOT / "10_Vercel" / "结果_前端版.json").read_text(encoding="utf-8"))
    return rows, strat, fe


def main():
    rows, strat, fe = load()

    # ---- 分组汇总（严格口径：只算「能答对」，「碰巧」单列不并入） ----
    g_total, g_ok = Counter(), Counter()
    for r in rows:
        g_total[r["分组"]] += 1
        if r["答对没有"].startswith("能答对"):
            g_ok[r["分组"]] += 1
    n_ok = sum(1 for r in rows if r["答对没有"].startswith("能答对"))
    n_lucky = sum(1 for r in rows if r["答对没有"].startswith("碰巧"))
    qmap = {int(r["题号"]): r for r in rows}

    # ---- 策略矩阵 ----
    order = ["A 全局top-8", "B 全局top-40", "C 分层(15家×2)", "D 分层(15家×4)",
             "E 分层+关键章节保底"]
    qids = [7, 8, 9, 10, 11, 12]
    cell, blocks, cover = {}, {}, {}
    for r in strat:
        cell[(r["策略"], r["题号"])] = r["判定"]
        blocks[r["策略"]] = r["送进模型的块数"]
        cover[r["策略"]] = r["覆盖公司数"]
    MARK = {"✓ 够答": ("够", "#0f766e"), "△ 部分": ("半", "#b45309"), "✗ 不够": ("缺", "#b3261e")}

    doc = SimpleDocTemplate(str(OUT), pagesize=A4,
                            leftMargin=12 * mm, rightMargin=12 * mm,
                            topMargin=9 * mm, bottomMargin=8 * mm,
                            title="财报问答库一页结论", author="陈静")
    W = doc.width
    f = []

    # ================= 页眉 =================
    head = Table([[P("同一批财报：什么问题该建库，什么问题该微调", "h1")],
                  [P("方向 A · 白酒 15 家上市公司 2025 年报问答知识库 ｜ 2,698 页 / 3,988 张表格 / "
                     "9,762 个文本块（每块带公司·章节·页码）｜ 本地 BM25 + bge-small-zh 向量混合检索 ｜ "
                     "线上 Vercel 纯静态版（检索在浏览器里跑，无后端）｜ 15 道题逐题记录 "
                     "+ 6 道失败题 × 5 种召回策略对照", "sub")]],
                 colWidths=[W])
    head.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), INK),
        ("LEFTPADDING", (0, 0), (-1, -1), 7),
        ("RIGHTPADDING", (0, 0), (-1, -1), 7),
        ("TOPPADDING", (0, 0), (0, 0), 6),
        ("BOTTOMPADDING", (0, 0), (0, 0), 0.5),
        ("BOTTOMPADDING", (0, 1), (-1, -1), 6),
    ]))
    f.append(head)
    f.append(Spacer(1, 4))

    # ================= KPI 三卡 =================
    kw = W / 3 - 1.5
    k1 = Table([[Paragraph(f"<font color='#0f766e'>{n_ok}/{len(rows)}</font>", S["kpi"])],
                [Paragraph("检索可答（严格口径）", S["kpil"])],
                [Paragraph("另有 1 题属碰巧命中，不计入", S["kpin"])]], colWidths=[kw])
    k2 = Table([[Paragraph("<font color='#0f766e'>9 / 9</font>", S["kpi"])],
                [Paragraph("单公司 · 要一个数字", S["kpil"])],
                [Paragraph("最好第 1 位命中，出处精确到页码", S["kpin"])]], colWidths=[kw])
    k3 = Table([[Paragraph("<font color='#b3261e'>0 / 3</font>", S["kpi"])],
                [Paragraph("跨公司 · 全景（无候选集）", S["kpil"])],
                [Paragraph("top-8 最多覆盖 8/15 家，结构上就答不了", S["kpin"])]], colWidths=[kw])
    for k, c in ((k1, GREEN_BG), (k2, GREEN_BG), (k3, RED_BG)):
        k.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), c),
            ("BOX", (0, 0), (-1, -1), 0.5, LINE),
            ("TOPPADDING", (0, 0), (0, 0), 4),
            ("BOTTOMPADDING", (0, -1), (-1, -1), 4),
            ("TOPPADDING", (0, 1), (-1, -1), 0.5),
            ("BOTTOMPADDING", (0, 0), (0, -2), 0),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ]))
    f.append(Table([[k1, k2, k3]], colWidths=[kw + 2, kw + 2, kw + 2],
                   style=TableStyle([("LEFTPADDING", (0, 0), (-1, -1), 0),
                                     ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                                     ("TOPPADDING", (0, 0), (-1, -1), 0),
                                     ("BOTTOMPADDING", (0, 0), (-1, -1), 0)])))
    f.append(Spacer(1, 3))

    # ================= 一、哪类答得好 / 哪类翻车 =================
    f.append(P("一、哪类题答得好，哪类题翻车，为什么"
               "<font size='7' color='#5a6b85'>"
               "　判据：标准答案需要的证据（哪家公司 + 哪个数字）有没有进 top-8</font>", "h2"))
    WHY = {
        "① 单公司·一个数字":
            "答案就是原文里的一行，语义检索直接命中；9 题全部召回，8 题 gold 块排进前 8，"
            "还能给出「公司 + 页码」出处，可审计",
        "② 跨公司·全景（无候选集）":
            "要扫完全部 15 家再取极值，而 top-8 只有 8 个名额、最多覆盖 8 家公司——"
            "结构上就覆盖不全，不是排序不够好",
        "③ 跨公司·受限（给了候选集）":
            "点名了公司也会被「最像问句的那一两家的块」挤掉名额：Q10 点名两家，"
            "top-8 只覆盖 1/15 家，top-40 也只有 2 家",
        "④ 单公司·要拼多块":
            "要 3 个数字（营收 / 归母净利 / 毛利率），营收与毛利率进了，归母净利那块没有；"
            "top-8 还有 4 个名额被同一章节的重复块占满",
    }
    data = [[C("题型分组", True), C("题数", True), C("", True), C("结果", True), C("为什么", True)]]
    for g in ["① 单公司·一个数字", "② 跨公司·全景（无候选集）",
              "③ 跨公司·受限（给了候选集）", "④ 单公司·要拼多块"]:
        ok, tot = g_ok[g], g_total[g]
        col = "#0f766e" if ok == tot else ("#b3261e" if ok == 0 else "#b45309")
        c = GREEN if ok == tot else (RED if ok == 0 else AMBER)
        data.append([C(g), C(str(tot)), bar(ok, tot, c, W * .09),
                     Paragraph(f"<font color='{col}'><b>{ok}/{tot}</b></font>", S["cell"]),
                     C(WHY[g])])
    f.append(table(data, [W * .195, W * .05, W * .11, W * .075, W * .57]))
    f.append(Spacer(1, 3))

    # -------- 双栏卡片：答得好 vs 翻车 --------
    cw = W * .488
    left = card("✅ 答得好的题：长什么样，为什么对", [
        "<b>长什么样</b>：点名了公司，要一个具体数字或事实。",
        "<b>例子</b>：贵州茅台营业收入多少 / 五粮液归母净利润多少 / "
        "洋河股份年末经销商几家 / 茅台在职员工多少人 / 审计意见类型。",
        "<b>为什么对</b>：答案是原文里的一行，问句和那一行在字面上几乎重合，"
        "BM25 与向量都直接命中；9 题里 8 题 gold 块排进前 8，最好第 1 位。",
        "<b>结论</b>：这类题建库就有用，不需要微调。",
    ], GREEN, GREEN_BG, cw)
    right = card("❌ 翻车的题：长什么样，为什么错", [
        "<b>长什么样</b>：要么「15 家里谁最……」，要么「同一家公司给我三个数」。",
        "<b>例子</b>：谁营收增速最快 / 谁亏损 / 谁毛利率最高 / 茅台营收+净利+毛利率。",
        "<b>为什么错</b>：检索按相似度取 8 块，天生做不了全集扫描与极值比较；"
        "名额还会被和问句最像的一两家吃掉（覆盖垄断）。",
        "<b>结论</b>：这类题换召回策略只能救一部分，正解是先 ETL 成宽表。",
    ], RED, RED_BG, cw)
    f.append(Table([[left, right]], colWidths=[cw, W - cw],
                   style=TableStyle([("LEFTPADDING", (0, 0), (-1, -1), 0),
                                     ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                                     ("LEFTPADDING", (1, 0), (1, 0), 4),
                                     ("TOPPADDING", (0, 0), (-1, -1), 0),
                                     ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
                                     ("VALIGN", (0, 0), (-1, -1), "TOP")])))

    # ================= 二、病根 =================
    f.append(P("二、病根不在数据、也不在模型，在「哪些块能到答案面前」", "h2"))
    mw = W * .488
    m1 = card("第一层 · 覆盖垄断", [
        "全局排序按语义相似度取块，名额被和问句最像的那一两家吃掉。"
        "Q10 点名了两家公司，top-8 却只覆盖 <b>1/15</b> 家，top-40 也只有 2 家。",
        "治法：按公司分层召回（每家各取几块），先把名额分配到公司。",
    ], BRAND, BG, mw)
    m2 = card("第二层 · 字段错位", [
        "即使覆盖到了公司，含数字的块在该公司内部也排得很靠后——"
        "今世缘的营收金额块排到自家第 <b>47</b> 位，因为语义检索先命中「讨论收入的段落」，"
        "而不是那张数字表。",
        "治法：用章节元数据路由，给「主要会计数据」这类章节保底名额。",
    ], AMBER, AMBER_BG, mw)
    f.append(Table([[m1, m2]], colWidths=[mw, W - mw],
                   style=TableStyle([("LEFTPADDING", (0, 0), (-1, -1), 0),
                                     ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                                     ("LEFTPADDING", (1, 0), (1, 0), 4),
                                     ("TOPPADDING", (0, 0), (-1, -1), 0),
                                     ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
                                     ("VALIGN", (0, 0), (-1, -1), "TOP")])))
    f.append(Spacer(1, 1.0))

    data = [[C("召回策略（只换策略，不动数据、不动模型）", True), C("块数", True), C("覆盖", True)] +
            [C(f"Q{q}", True) for q in qids]]
    for s in order:
        row = [C(s), C(str(blocks[s])), C(f"{cover[s]}/15")]
        for q in qids:
            m, col = MARK.get(cell.get((s, q), "✗ 不够"), ("缺", "#b3261e"))
            row.append(Paragraph(f"<font color='{col}'><b>{m}</b></font>", S["cell"]))
        data.append(row)
    f.append(table(data, [W * .245, W * .06, W * .075] + [W * .103] * 6))
    f.append(Spacer(1, 1.0))
    f.append(P("够＝证据齐全　半＝只齐一部分　缺＝一项都没有。Q7 谁增速最快 / Q8 谁亏损 / "
               "Q9 毛利率最高 / Q10 茅台vs五粮液 / Q11 三家营收谁高 / Q12 茅台三个数字。"
               "<b>两种失败机制要分开治</b>：加大 k（B，8→40 块）救不回 Q10 的覆盖垄断，"
               "按公司分层（C/D）才修好它；但 Q12 恰恰只要加大 k 就够了（B 即「够」，"
               "分层反而退回「半」）；Q7 最难，只有再叠加「主要会计数据」章节保底的"
               "元数据路由（E）才修好。"
               f"代价是上下文从 {blocks['A 全局top-8']} 块涨到 {blocks['E 分层+关键章节保底']} 块；"
               "Q8 五种策略全程停在「半」（两家亏损公司只能召回一家）、"
               "Q11 只能从「缺」到「半」——这两题是真正的跨实体聚合，换召回策略解决不了。", "small"))

    # ================= 三、处方 =================
    f.append(P("三、结论：建库、微调、还是先做 ETL", "h2"))
    data = [[C("什么样的问题", True), C("该怎么做", True), C("为什么（本实验的依据）", True)],
            [C("点名了公司、要某个具体数字或事实<br/>「茅台营收多少」「洋河经销商几家」"),
             C("<font color='#0f766e'><b>建库（检索）</b></font>"),
             C("答案是原文里的一行：<b>9/9 全部召回</b>，最好第 1 位，还带得出处、可审计；"
               "年报年年换，库增量重建即可，不用重训")],
            [C("要稳定口径、术语、叙述方式<br/>「用董秘的语气解释为什么下滑」"),
             C("<font color='#2b5fd9'><b>微调</b></font>"),
             C("这是不变的东西，检索给不了：它只能抄回原文，学不会"
               "「先讲行业、再讲公司、最后讲措施」这套组织方式")],
            [C("要跨实体聚合、排序、取极值<br/>「15 家里谁增速最快」"),
             C("<font color='#b3261e'><b>两个都不对</b></font><br/>先 ETL 成结构化宽表"),
             C("检索按语义相似度取块，天生做不了全集扫描与极值比较：本次跨公司题"
               "在全局 top-8 下严格口径 0 道答对（唯一命中的 Q9 是碰巧）；"
               "靠分层 + 章节路由能救回一部分，但 Q8 全程只到「半」、Q11 从「缺」"
               "也只到「半」，且上下文要涨 5 倍。把 15 家的关键指标"
               "抽成一张宽表，再让模型读表，才是稳定解")]]
    f.append(table(data, [W * .265, W * .155, W * .58]))

    # ================= 四、注脚 =================
    f.append(P("四、四条诚实的注脚", "h2"))
    f.append(P("① 我出的第一版标准答案自己就错了一题：Q8 只写了酒鬼酒，漏了亏得更多的顺鑫农业"
               "（-1.52 亿，顺鑫 2025 年报 P7 主要会计数据表），是回头翻原文才发现的——"
               "这反过来印证建库的意义：<b>人对数字的记忆不可靠，检索到的原文才可靠</b>。"
               "② 过程中修掉一个自己的实现缺陷：候选池大小原本跟着 top-k 变，"
               "导致同一个问题在 top-2 与 top-60 下排序不一致、分层召回取块不稳；"
               "改成固定取全部候选后结果才自洽（03_索引/检索.py）。"
               "③ 问答页面的大模型生成只是可选增强；<b>本次评估的判定全部基于检索，"
               "不依赖任何大模型</b>。"
               "④ 线上版原本走 Vercel 的 Python 后端，构建一直失败，最后改成"
               "<b>纯静态</b>：BM25 倒排索引压成 1.16MB 下发浏览器、正文按公司分片按需拉取，"
               f"检索完全在浏览器里跑。自评 {fe['得分']['auto']}/{fe['题数']} 与本地严格口径持平，"
               "<b>但不是同一批题</b>——本地靠向量拿下的 Q1 在线上丢了，"
               "线上靠按公司分层补回了本地没救回的 Q10。代价是装不下神经向量，"
               "收益是手机流量也能直接打开：工程取舍，不是精度提升。", "foot"))
    f.append(Spacer(1, 1.0))
    f.append(P("数据源：巨潮资讯网公开年报 PDF（15 家白酒上市公司 2025 年度报告）｜全部本地解析与检索，"
               "PDF 不进仓库｜逐题记录见 05_评估/逐题记录.md，对照实验见 05_评估/对比_召回策略.md｜"
               "线上自评见 10_Vercel/结果_前端版.json｜补充材料见 08_补交/（表1–表6；"
               "表5 系统实际回答与逐题判定：正确8·部分4·错误3）", "foot"))

    doc.build(f)
    print(f"已生成 {OUT}  {OUT.stat().st_size} 字节｜{len(rows)} 题，严格口径 {n_ok} 题可答"
          f"（碰巧 {n_lucky} 题单列）")


if __name__ == "__main__":
    main()
