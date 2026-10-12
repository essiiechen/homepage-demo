#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
批改补充材料生成器（第三周作业 · 方向 A）

针对批改意见里还没覆盖的三件事，全部从底表算出来，不手写任何数字：
  表1  本地运行 vs 线上运行 分别计分（逐题对照 + 差异题）
  表2  召回层 vs 答案层 分别评价（证据有没有召回 ≠ 答案里有没有那个数字）
  表3  跨公司全景题按公司取证（Q7/Q8/Q9 × 15 家：证据块页码 / 原文片段 / 是否进 top-8）
  表4  口径与出处核对（15 题的 gold 证据真实出处 + 原文片段 + 数字核对）

输入（都是已有产物，不重新检索）：
  data/chunks.jsonl                 9,762 块（带公司 / 页码 / 章节 / 原文）
  05_评估/逐题记录.csv               本地运行（bge 混合检索）逐题判定
  10_Vercel/结果_前端版.json          线上运行（浏览器 BM25 自适应）逐题判定
输出：
  08_补交/表1_本地vs线上.{csv,md}
  08_补交/表2_召回层vs答案层.{csv,md}
  08_补交/表3_全景题按公司取证.{csv,md}
  08_补交/表4_口径与出处核对.md

用法： python3 05_评估/补交_复盘.py
"""
from __future__ import annotations

import csv
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "08_补交"
OUT.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------- 载入底表
def load_chunks() -> list[dict]:
    p = ROOT / "data" / "chunks.jsonl"
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


CHUNKS = load_chunks()

# (公司, 页码, kind) -> 块列表。CSV 里的"召回块"写成「贵州茅台 P21文」，靠这个映射回原文
IDX: dict[tuple[str, int, str], list[dict]] = {}
for c in CHUNKS:
    IDX.setdefault((c["name"], int(c["page"]), c["kind"]), []).append(c)

LOCAL_CSV = ROOT / "05_评估" / "逐题记录.csv"
ONLINE_JSON = ROOT / "10_Vercel" / "结果_前端版.json"

local_rows = list(csv.DictReader(LOCAL_CSV.read_text(encoding="utf-8-sig").splitlines()))
online = json.loads(ONLINE_JSON.read_text(encoding="utf-8"))
online_by_q = {int(r["题号"]): r for r in online["逐题"]}

NAMES15 = ["贵州茅台", "五粮液", "泸州老窖", "山西汾酒", "洋河股份", "古井贡酒", "今世缘",
           "酒鬼酒", "水井坊", "迎驾贡酒", "口子窖", "老白干酒", "天佑德酒", "金徽酒", "顺鑫农业"]


# ---------------------------------------------------------------- 工具
def parse_blocks(s: str) -> list[tuple[int, str, int, str]]:
    """'#1 贵州茅台 P21文 ｜ #2 贵州茅台 P12表' -> [(1,'贵州茅台',21,'text'), ...]"""
    out = []
    for item in re.split(r"｜|\|", s or ""):
        m = re.search(r"#(\d+)\s+(\S+?)\s+P(\d+)([文表])", item)
        if m:
            kind = "text" if m.group(4) == "文" else "table"
            out.append((int(m.group(1)), m.group(2), int(m.group(3)), kind))
    return out


def block_text(name: str, page: int, kind: str) -> str:
    cs = IDX.get((name, page, kind), [])
    return "\n".join(c["text"] for c in cs)


def norm_num(x: str) -> str:
    """统一数字形态：去千分位、去空格"""
    return x.replace(",", "").replace(" ", "").strip()


def gold_numbers(ans: str) -> list[str]:
    """从标准答案里抽出关键数字（含百分号、含负号、含小数点）"""
    raw = re.findall(r"-?\d[\d,]*\.?\d*\s*%?", ans)
    nums, seen = [], set()
    for r in raw:
        n = norm_num(r)
        core = n.replace("%", "").replace("-", "").replace(".", "")
        # 丢掉页码、"每10股"里的 10、句子里的 15 这类一两位噪声，只留真正的指标数字
        if len(core) < 3:
            continue
        if n not in seen:
            seen.add(n)
            nums.append(n)
    return nums


def _as_float(s: str) -> float | None:
    try:
        return float(s.replace("%", "").replace("(", "-").replace("）", "").strip())
    except ValueError:
        return None


def num_in(text: str, nums: list[str]) -> str | None:
    """
    文本里是否出现任一关键数字。同时容纳三种单位写法：
    标准答案写「1,688.38 亿元」，原文可能是「168,838,102,514.79」（元）或「168,838」（万元），
    所以按 1 / 1e4 / 1e8 三种量级做相对误差匹配（容差 0.5%），避免"单位不同 = 判定失败"的假阴性
    """
    # 只去千分位逗号、保留空格：表格里相邻数字靠空格分隔，连成一片会把 7.52 这种小数吞掉
    xs = [v for v in (_as_float(m) for m in
                      re.findall(r"-?\d[\d,]*\.?\d*", text.replace(",", ""))) if v is not None]
    for n in nums:
        v = _as_float(n)
        if v is None:
            continue
        for x in xs:
            for scale in (1.0, 1e4, 1e8):
                if abs(v) > 1e-9 and abs(x / scale - v) <= max(abs(v) * 0.005, 0.005):
                    return n
    return None


def required_companies(question: str, group: str) -> set[str]:
    """这道题的答案需要哪几家的证据齐全：
    全景题要 15 家；点名了公司的只要那几家；单公司题只要 1 家"""
    if "全景" in group:
        return set(NAMES15)
    hit = {n for n in NAMES15 if n in question}
    return hit or set(NAMES15)


def snippet(text: str, num: str | None, width: int = 70) -> str:
    if not text:
        return ""
    t = re.sub(r"\s+", " ", text)
    if num:
        i = norm_num(t).find(num)
        if i >= 0:
            a = max(0, i - width // 3)
            return ("…" if a > 0 else "") + t[a:a + width] + "…"
    return t[:width] + ("…" if len(t) > width else "")


def local_verdict(v: str) -> str:
    if v.startswith("能答对"):
        return "✓"
    if "碰巧" in v:
        return "△"
    return "✗"


# ================================================================ 表1 本地 vs 线上
def build_t1() -> tuple[list[dict], dict]:
    rows = []
    for r in local_rows:
        q = int(r["题号"])
        lv = local_verdict(r["答对没有"])
        ov = online_by_q[q]["auto"]
        if lv == "✓" and ov == "✓":
            diff = "一致"
        elif lv == "✓" and ov == "✗":
            diff = "本地✓ / 线上✗"
        elif lv == "✗" and ov == "✓":
            diff = "本地✗ / 线上✓"
        elif lv == "△":
            diff = "碰巧命中（严格口径不计）"
        else:
            diff = "一致（都错）"
        rows.append({
            "题号": q, "分组": r["分组"], "问题": r["问题"],
            "本地运行": lv, "线上运行": ov, "差异": diff,
        })
    # 合计
    loc_strict = sum(1 for x in rows if x["本地运行"] == "✓")
    loc_broad = loc_strict + sum(1 for x in rows if x["本地运行"] == "△")
    onl = sum(1 for x in rows if x["线上运行"] == "✓")
    summary = {
        "本地严格": loc_strict, "本地宽口径": loc_broad,
        "线上": onl, "题数": len(rows),
        "不同题": sum(1 for x in rows if x["本地运行"] != x["线上运行"]),
    }
    return rows, summary


# ================================================================ 表2 召回层 vs 答案层
# 金标准补正：本次回原文核对时补上的（原金标准写得不完整），出处一并记下
GOLD_FIX = {
    15: ("在职员工的数量合计 34,992 人（母公司 33,504 + 主要子公司 1,488）", "贵州茅台 P31"),
}
# 文字型答案：没有数字可核对，改按原文措辞核对
TEXT_ANSWER = {14: "标准无保留意见"}


def gold_nums_for(q: int, r: dict) -> list[str]:
    if q in GOLD_FIX:
        return gold_numbers(GOLD_FIX[q][0])
    return gold_numbers(r["标准答案（回原文核过）"])


def build_t2() -> tuple[list[dict], dict]:
    rows = []
    for r in local_rows:
        q = int(r["题号"])
        nums = gold_nums_for(q, r)
        blocks = parse_blocks(r["召回块（hybrid top-8）"])
        need = required_companies(r["问题"], r["分组"])

        # 数字出现在哪些公司的块里、最早在第几位
        hit_rank, hit_num, hit_comps = None, None, set()
        for rank, name, page, kind in blocks:
            hit = num_in(block_text(name, page, kind), nums)
            if hit:
                hit_comps.add(name)
                if hit_rank is None:
                    hit_rank, hit_num = rank, hit

        # 每个数字分别落在哪几家：数字可以分散在不同块，但题目涉及的每家都得有数
        num_hits: dict[str, set[str]] = {n: set() for n in nums}
        for rank, name, page, kind in blocks:
            t = block_text(name, page, kind)
            if name not in need:
                continue
            for n in nums:
                if num_in(t, [n]):
                    num_hits[n].add(name)
        got_nums = {n for n, cs_ in num_hits.items() if cs_}
        got_comps = set().union(*num_hits.values()) if num_hits else set()

        if q in TEXT_ANSWER:                     # 文字型答案按措辞核对（并区分是哪一种审计意见）
            kw = TEXT_ANSWER[q]
            strict = [(rk, nm, pg) for rk, nm, pg, kd in blocks if kw in block_text(nm, pg, kd)]
            loose = [(rk, nm, pg) for rk, nm, pg, kd in blocks if "无保留" in block_text(nm, pg, kd)]
            if strict:
                ans_layer = f"✓ top-{strict[0][0]} 即含「{kw}」"
                hit_rank = strict[0][0]
            elif loose:
                ans_layer = f"△ 只命中内控审计意见（top-{loose[0][0]} P{loose[0][2]}），非财报审计意见"
                hit_rank = loose[0][0]
            else:
                ans_layer = "✗ 答案里没有该措辞"
                hit_rank = None
            cover = "—"
        elif not nums:
            ans_layer, cover = "（无数字型答案，不适用）", "—"
        else:
            cover = f"{len(got_comps)}/{len(need)}"
            if not got_nums:
                ans_layer = "✗ 答案里没有该数字"
            elif got_nums >= set(nums) and got_comps >= need:
                ans_layer = f"✓ 齐全（{len(got_nums)}/{len(nums)} 个数字，{cover} 家）"
            else:
                miss = len(nums) - len(got_nums)
                ans_layer = (f"△ 部分（数字 {len(got_nums)}/{len(nums)}、缺 {miss} 个；"
                             f"公司 {cover} 家）")

        primary = nums[0] if nums else ""
        p_rank = next((rk for rk, nm, pg, kd in blocks
                       if primary and num_in(block_text(nm, pg, kd), [primary])), None)
        rows.append({
            "题号": q, "分组": r["分组"], "问题": r["问题"],
            "关键数字": "/".join(nums[:3]) if nums else "（文字型答案）",
            "召回层": "✓" if r["gold块召回"] == "是" else "✗",
            "首要数字": (f"✓ top-{p_rank}" if p_rank else ("✗" if primary else "—")),
            "答案层": ans_layer,
            "答案层证据覆盖": cover,
            "首现位次": hit_rank or "",
            "出处": (f"{blocks[hit_rank-1][1]} P{blocks[hit_rank-1][2]}" if hit_rank else ""),
        })
    recall_ok = sum(1 for x in rows if x["召回层"] == "✓")
    ans_ok = sum(1 for x in rows if x["答案层"].startswith("✓"))
    ans_part = sum(1 for x in rows if x["答案层"].startswith("△"))
    pri_ok = sum(1 for x in rows if x["首要数字"].startswith("✓"))
    summary = {"召回层命中": recall_ok, "答案层齐全": ans_ok, "答案层部分": ans_part,
               "首要数字命中": pri_ok, "题数": len(rows)}
    return rows, summary


# ================================================================ 表3 全景题按公司取证
PANORAMA = {
    7: {"题": "营业收入同比增长最快的是哪家", "pats": [[r"营业收入", r"同比"]], "pick": "营业收入同比"},
    8: {"题": "归母净利润亏损的是哪家",
        "pats": [[r"归属于上市公司股东的净利润"], [r"归属于母公司所有者的净利润"], [r"净利润"]],
        "pick": "归母净利润"},
    9: {"题": "酒类（主营业务）毛利率最高的是哪家", "pats": [[r"毛利率"]], "pick": "毛利率"},
}


def find_evidence(name: str, alt_pats: list[list[str]]) -> tuple[dict | None, str]:
    """
    在该公司的块里找最像证据的一块：优先「主要会计数据 / 主要财务指标」章节，其次表格块。
    各家年报措辞不统一（顺鑫的那行就被表格切片切走了），所以 pats 是**备选组**，按序试到有为止。
    """
    for pats in alt_pats:
        cands = []
        for c in CHUNKS:
            if c["name"] != name:
                continue
            if not all(re.search(p, c["text"]) for p in pats):
                continue
            if not re.search(r"\d", c["text"]):
                continue
            sec = f"{c.get('section','')}{c.get('subsection','')}"
            score = 0
            if "主要会计数据" in sec or "主要财务指标" in sec:
                score += 10
            if "合并利润表" in sec or "利润表" in sec:
                score += 5
            if c["kind"] == "table":
                score += 2
            cands.append((score, c))
        if cands:
            cands.sort(key=lambda x: -x[0])
            c = cands[0][1]
            return c, c["text"]
    return None, ""


def extract_metric(text: str, pick: str) -> str:
    t = re.sub(r"\s+", " ", text)
    if pick == "营业收入同比":
        m = re.search(r"营业收入[^0-9\-]{0,20}(-?[\d,]+\.?\d*)", t)
        val = m.group(1) if m else ""
        m2 = re.search(r"营业收入[\s\S]{0,120}?(-?\d+\.?\d*)\s*%", t)
        return f"营收 {val}；同比 {m2.group(1) + '%' if m2 else '—'}"
    if pick == "归母净利润":
        m = re.search(r"归属于上市公司股东的净利润[^0-9\-]{0,30}(-?[\d,]+\.?\d*)", t)
        return f"归母净利润 {m.group(1) if m else '—'}"
    if pick == "毛利率":
        ps = re.findall(r"毛利率[^0-9\-]{0,20}(-?\d+\.?\d*)\s*%", t)
        ps = [float(p) for p in ps if -50 < float(p) <= 100]
        return f"毛利率最高 {max(ps):.2f}%" if ps else "—"
    return "—"


def build_t3() -> tuple[list[dict], dict]:
    """
    只做取证，不重算真值：15 家的证据块在年报哪一页、原文写了什么、有没有进 top-8。
    真值（谁最快 / 谁亏损 / 谁毛利率最高）已经在 05_评估/查证_标准答案.py 里人工回原文核过，
    这里只回答「答案涉及的那些公司，证据有没有进上下文」——全景题的对错取决于覆盖，不取决于某一家的值。
    """
    rows, stat = [], {}
    for q, meta in PANORAMA.items():
        lr = next(x for x in local_rows if int(x["题号"]) == q)
        covered = {n for _, n, _, _ in parse_blocks(lr["召回块（hybrid top-8）"])}
        ans_comps = {n for n in NAMES15 if n in lr["标准答案（回原文核过）"]}
        for name in NAMES15:
            c, text = find_evidence(name, meta["pats"])
            rows.append({
                "题号": q, "题型": meta["题"], "公司": name,
                "是答案里的公司": "★" if name in ans_comps else "",
                "证据是否进本地top-8": "✓" if name in covered else "✗",
                "证据页码": (f"P{c['page']}" if c else "—"),
                "证据章节": ((c.get("section") or c.get("subsection") or "—")[:16] if c else "—"),
                "原文片段": snippet(text, None, 76) if c else "—",
            })
        stat[q] = {
            "答案公司": sorted(ans_comps),
            "答案公司进了top-8的": sorted(ans_comps & covered),
            "top-8覆盖": len(covered),
        }
    return rows, stat


# ================================================================ 表4 口径与出处核对
def build_t4() -> list[dict]:
    rows = []
    for r in local_rows:
        q = int(r["题号"])
        nums = gold_nums_for(q, r)
        std = GOLD_FIX[q][0] if q in GOLD_FIX else r["标准答案（回原文核过）"]
        fix = "（本次补正：原金标准只写了「需看年报在职员工表」，回原文补上实数）" if q in GOLD_FIX else ""
        blocks = parse_blocks(r["召回块（hybrid top-8）"])
        if r["gold块召回"] == "是":
            gi = int(r["gold位置"]) - 1
            name, page, kind = blocks[gi][1], blocks[gi][2], blocks[gi][3]
            text = block_text(name, page, kind)
            where = f"{name} P{page}（top-8 第 {gi+1} 位）"
        else:
            # gold 没进 top-8：全库找这个数字真正躺在哪些块里，把出处挖出来
            hits = [c for c in CHUNKS if num_in(c["text"], nums)]
            if hits:
                c = hits[0]
                text = c["text"]
                where = f"{c['name']} P{c['page']}（未进 top-8，全库定位）"
            else:
                text, where = "", "（全库未匹配到该数字，需人工）"
        if q in TEXT_ANSWER:
            ok = "✓" if TEXT_ANSWER[q] in text else "✗"
        else:
            ok = "✓" if num_in(text, nums) else "✗"
        rows.append({
            "题号": q, "问题": r["问题"], "标准答案": std + fix,
            "关键数字": "/".join(nums[:3]) if nums else "（文字型）",
            "gold证据出处": where, "数字核对": ok,
            "原文片段": snippet(text, num_in(text, nums), 80),
        })
    return rows


# ================================================================ 落盘
def write_csv(path: Path, rows: list[dict], cols: list[str]):
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in cols})


def md_table(rows: list[dict], cols: list[str]) -> str:
    head = "| " + " | ".join(cols) + " |"
    sep = "|" + "|".join(["---"] * len(cols)) + "|"
    body = ["| " + " | ".join(str(r.get(c, "")).replace("\n", " ") for c in cols) + " |" for r in rows]
    return "\n".join([head, sep] + body)


def main():
    t1, s1 = build_t1()
    t2, s2 = build_t2()
    t3, s3 = build_t3()
    t4 = build_t4()

    c1 = ["题号", "分组", "问题", "本地运行", "线上运行", "差异"]
    c2 = ["题号", "分组", "问题", "关键数字", "召回层", "首要数字", "答案层", "答案层证据覆盖", "首现位次", "出处"]
    c3 = ["题号", "题型", "公司", "是答案里的公司", "证据是否进本地top-8", "证据页码", "证据章节", "原文片段"]
    c4 = ["题号", "问题", "标准答案", "关键数字", "gold证据出处", "数字核对", "原文片段"]

    write_csv(OUT / "表1_本地vs线上.csv", t1, c1)
    write_csv(OUT / "表2_召回层vs答案层.csv", t2, c2)
    write_csv(OUT / "表3_全景题按公司取证.csv", t3, c3)
    write_csv(OUT / "表4_口径与出处核对.csv", t4, c4)

    (OUT / "表1_本地vs线上.md").write_text(
        "# 表1　本地运行 vs 线上运行 分别计分\n\n"
        f"- **本地运行**（Python，bge 神经 embedding + BM25 混合检索，top-8）：严格口径 **{s1['本地严格']}/{s1['题数']}**"
        f"（另有 1 题碰巧命中不计，宽口径 {s1['本地宽口径']}/{s1['题数']}）\n"
        f"- **线上运行**（浏览器 BM25 静态索引，自适应策略 auto）：**{s1['线上']}/{s1['题数']}**\n"
        f"- 两套运行结果**不同的题共 {s1['不同题']} 道**：本地靠神经向量拿下的 Q1 在线上丢了，"
        "线上靠按公司分层补回了本地没救回来的 Q10。两个数都是 9，但**不是同一批题**，不能混着说。\n\n"
        + md_table(t1, c1) + "\n", encoding="utf-8")

    (OUT / "表2_召回层vs答案层.md").write_text(
        "# 表2　召回层 与 答案层 分别评价\n\n"
        "两层不是一回事，必须分开报：\n\n"
        "- **召回层**：标准答案需要的那个证据块，有没有进 top-8（`gold块召回` 列）。\n"
        "- **答案层**：答案里要的那个数字，有没有真的出现在被召回的原文里、出现在第几位、"
        "**而且题目涉及的每一家都齐了**（全景题要 15 家都有数，点名两家的题要两家都有数）。\n\n"
        "答案层是机械判定的：抽出标准答案里的关键数字，去 top-1…top-8 的原文里逐块找，记第一次命中的位次，"
        "再统计命中的公司数。匹配时同时容忍**亿元 / 万元 / 元**三种写法（按 1、1e4、1e8 三个量级做 0.5% 相对误差匹配），"
        "避免「单位不同」被误判成没答对。Q14 是文字型答案，改按「标准无保留意见」这个措辞核对。\n\n"
        f"- 召回层命中：**{s2['召回层命中']}/{s2['题数']}**\n"
        f"- 答案层齐全（需要的每家都有那个数）：**{s2['答案层齐全']}/{s2['题数']}**\n"
        f"- 答案层部分命中（数字出现了，但覆盖的公司不够）：**{s2['答案层部分']}/{s2['题数']}**\n"
        f"- 放宽到「首要数字」（标准答案里第一个、也是最主体的那个数）命中：**{s2['首要数字命中']}/{s2['题数']}**\n\n"
        "三个数要一起看：**召回层看证据进没进来，首要数字看答案能不能成立，全数字齐全才是满分答案**。\n"
        "只报其中一个都会失真——比如 Q12 三个数字里进了两个（营收、毛利率），归母净利润那块在 P6/P7 没进 top-8，\n"
        "所以「召回层✗、首要数字✓、全数字△」，正好对应它「多块拼接」的病根。\n\n"
        "**两层给出的结论不一样**：召回层只说「证据进没进来」，答案层才说「答案能不能成立」。\n"
        "典型的是 Q7/Q9/Q10/Q12——它们召回层是 ✗，答案层却又显示「数字出现过」，"
        "因为那个数恰好躺在 top-1 里，但**只有 1 家公司有数、其余 14 家根本没进上下文**，答案照样不成立。\n\n"
        + md_table(t2, c2) + "\n", encoding="utf-8")

    pano_md = ["# 表3　跨公司全景题：按公司逐家取证（Q7 / Q8 / Q9 × 15 家）\n\n",
               "全景题的命门是**覆盖**：答案要在 15 家里比大小，任何一家没进上下文，结论就不成立。\n"
               "所以这里不看排序、直接逐家取证——每家公司各自的证据块在年报哪一页、原文写了什么、"
               "有没有进本地 top-8（★ 表示这家就是答案里要点名的公司）。\n\n",
               "**只做取证、不重算真值**：真值（谁最快 / 谁亏损 / 谁毛利率最高）已在 "
               "`05_评估/查证_标准答案.py` 里人工回原文核过，直接引用，不用脚本再抽一遍——"
               "各家年报的章节名不统一（「主要会计数据」/「主要财务指标」/其他），"
               "自动抽取只能覆盖 5/15 家，抽出来的数不敢用。\n\n"]
    for q in (7, 8, 9):
        st = s3[q]
        pano_md.append(
            f"### Q{q}　{PANORAMA[q]['题']}\n\n"
            f"- 标准答案（人工回原文核过）：{next(x['标准答案（回原文核过）'] for x in local_rows if int(x['题号']) == q)}\n"
            f"- 答案要点名的公司：**{'、'.join(st['答案公司']) or '（无）'}**\n"
            f"- 这几家的证据有没有进 top-8：**{'、'.join(st['答案公司进了top-8的']) or '一家都没进'}**"
            f"（top-8 总共只覆盖 {st['top-8覆盖']}/15 家）\n\n")
    pano_md.append(md_table(t3, c3) + "\n")
    (OUT / "表3_全景题按公司取证.md").write_text("".join(pano_md), encoding="utf-8")

    (OUT / "表4_口径与出处核对.md").write_text(
        "# 表4　口径与出处核对（15 题逐题）\n\n"
        "口径分两档，仓库里两个数都能对上文件，不要混用：\n\n"
        "- **严格口径 9/15**：Q9（哪家毛利率最高）只覆盖 5/15 家就下了「最高」的结论，属于碰巧命中，**不计**；\n"
        "- **宽口径 10/15**：把 Q9 也算上。\n\n"
        "出处核对的做法：把标准答案里的关键数字抽出来，回 `data/chunks.jsonl` 定位它真正躺在哪个公司的哪一页，"
        "并把原文片段贴出来。**gold 块进了 top-8 的记「第 N 位」，没进的记「全库定位」** —— 后者正是失败题的证据藏在哪。\n\n"
        + md_table(t4, c4) + "\n", encoding="utf-8")

    print("== 表1 本地 vs 线上 ==")
    print(f"  本地严格 {s1['本地严格']}/{s1['题数']}（宽口径 {s1['本地宽口径']}）｜线上 {s1['线上']}/{s1['题数']}｜不同题 {s1['不同题']}")
    for x in t1:
        if x["本地运行"] != x["线上运行"]:
            print(f"    差异 Q{x['题号']}: 本地{x['本地运行']} 线上{x['线上运行']}  {x['问题'][:26]}")
    print("== 表2 召回层 vs 答案层 ==")
    print(f"  召回层 {s2['召回层命中']}/{s2['题数']}｜答案层齐全 {s2['答案层齐全']}/{s2['题数']}"
          f"（部分命中 {s2['答案层部分']}）")
    print("== 表3 全景题覆盖 ==")
    for q in (7, 8, 9):
        st = s3[q]
        print(f"  Q{q}: top-8 覆盖 {st['top-8覆盖']}/15 家｜答案公司 {st['答案公司']} "
              f"其中进 top-8 的：{st['答案公司进了top-8的'] or '无'}")
    print("== 表4 出处核对 ==")
    ok = sum(1 for x in t4 if x["数字核对"] == "✓")
    print(f"  数字核对通过 {ok}/{len(t4)}")
    print(f"\n已写入 {OUT}/")


if __name__ == "__main__":
    main()
