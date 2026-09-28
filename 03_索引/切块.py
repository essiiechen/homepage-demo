#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
把页级 JSONL 切成带元数据的块（chunk）。

切块原则：
  · 按财报自身的章节结构切，不按固定字数硬切 —— 章节是有语义的单位
  · 每个块都带：公司、股票代码、章节路径、页码（PDF 页 + 报告印刷页码）、类型
  · 表格单独成块，并带上「表格前最近的正文」作为上下文（否则表格丢了表头就没人认得）
  · 大表格按行分片，每片重复表头行（保证每片自解释）
  · 页眉页脚按出现频次自动识别并剔除

产出 data/chunks.jsonl：
  {"chunk_id","code","name","page","printed_page","section","subsection","kind","text","n_chars"}
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PAGES_DIR = ROOT / "data" / "pages"
OUT = ROOT / "data" / "chunks.jsonl"

MAX_CHARS = 700      # 文本块目标上限
OVERLAP = 100        # 超长块的滑窗重叠
MIN_CHARS = 30       # 太短的块并入上一个块
CTX_CHARS = 120      # 表格块携带的前文长度
TBL_ROWS_PER_SHARD = 20

SEC_RE = re.compile(r"^第[一二三四五六七八九十百]+[节篇部分]\s*(.*)$")
SUB_RE = re.compile(r"^([一二三四五六七八九十]+)、\s*(\S.{0,40})$")
SUB2_RE = re.compile(r"^（([一二三四五六七八九十]+)）\s*(\S.{0,40})$")
SUB3_RE = re.compile(r"^(\d{1,2})[、.]\s*(\S.{0,40})$")
TOC_RE = re.compile(r"\.{6,}|…{3,}|　{4,}")          # 目录页的点线引导
PAGENO_RE = re.compile(r"^\s*(\d{1,4}\s*/\s*\d{1,4}|\d{1,4}|[-—–]\s*\d{1,4}\s*[-—–]?)\s*$")
PAGEFOOT_A = re.compile(r"^\s*(\d{1,3})\s*/\s*(\d{1,3})\s*$")   # 3/143
PAGEFOOT_B = re.compile(r"^\s*[-—–]\s*(\d{1,3})\s*[-—–]?\s*$")  # - 10 -
CN_NUM = "一二三四五六七八九十"


def printed_page(text: str) -> int | None:
    """从页首/页尾找报告印刷页码（不同公司格式不同，找不到就返回 None）。"""
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    probe = lines[:2] + lines[-3:]
    for ln in probe:
        m = PAGEFOOT_A.match(ln)
        if m:
            v = int(m.group(1))
            return v if 1 <= v <= 999 else None
        m = PAGEFOOT_B.match(ln)
        if m:
            v = int(m.group(1))
            return v if 1 <= v <= 999 else None
    return None


def detect_boilerplate(pages: list[dict]) -> set[str]:
    """出现于 >=35% 页面的短行 = 页眉/页脚，切块时剔除。"""
    n = len(pages)
    cnt: Counter[str] = Counter()
    for p in pages:
        seen = set()
        for ln in (p["text"] or "").split("\n"):
            s = ln.strip()
            if 0 < len(s) <= 45:
                seen.add(s)
        cnt.update(seen)
    thresh = max(4, int(n * 0.35))
    return {s for s, c in cnt.items() if c >= thresh}


def flush(buf: list[str], out: list[dict], meta: dict) -> None:
    """把累积的行写出成块，超长则滑窗切分。"""
    txt = "\n".join(x for x in buf if x).strip()
    if len(txt) < MIN_CHARS:
        if out and out[-1]["kind"] == "text" and out[-1]["code"] == meta["code"]:
            out[-1]["text"] += "\n" + txt
            out[-1]["n_chars"] = len(out[-1]["text"])
        return
    if len(txt) <= MAX_CHARS:
        out.append({**meta, "kind": "text", "text": txt, "n_chars": len(txt)})
        return
    step = MAX_CHARS - OVERLAP
    i = 0
    while i < len(txt):
        piece = txt[i:i + MAX_CHARS]
        if len(piece) >= MIN_CHARS:
            out.append({**meta, "kind": "text", "text": piece, "n_chars": len(piece)})
        i += step


def shard_table(md: str, rows: list[list[str]], header_ctx: str, meta: dict,
                out: list[dict]) -> None:
    """表格成块；行数过多时按行分片并重复表头。"""
    if len(rows) <= TBL_ROWS_PER_SHARD or not rows:
        body = md
        out.append({**meta, "kind": "table", "text": f"[表格] {header_ctx}\n{body}",
                    "n_chars": len(body)})
        return
    head = rows[0]
    body_rows = rows[1:]
    for k in range(0, len(body_rows), TBL_ROWS_PER_SHARD):
        part = [head] + body_rows[k:k + TBL_ROWS_PER_SHARD]
        md_part = "\n".join(
            "| " + " | ".join(c or "" for c in r) + " |" for r in part
        )
        tag = f"（第{k//TBL_ROWS_PER_SHARD+1}片）" if len(body_rows) > TBL_ROWS_PER_SHARD else ""
        txt = f"[表格{tag}] {header_ctx}\n{md_part}"
        out.append({**meta, "kind": "table", "text": txt, "n_chars": len(txt)})


def chunk_one(code: str, name: str) -> list[dict]:
    pages = [json.loads(l) for l in
             (PAGES_DIR / f"{code}.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    pages.sort(key=lambda p: p["page"])
    boiler = detect_boilerplate(pages)

    out: list[dict] = []
    section, subsection = "", ""
    buf: list[str] = []

    for p in pages:
        page_no = p["page"]
        pp = printed_page(p.get("text", ""))
        meta = {"code": code, "name": name, "page": page_no, "printed_page": pp,
                "section": section, "subsection": subsection}

        # --- 先处理表格：表格前最近的正文作为上下文 ---
        tbl_ctx = ""
        for t in p.get("tables", []):
            ctx = (tbl_ctx or section or name)[-CTX_CHARS:]
            shard_table(t["md"], t.get("rows", []), ctx.strip(), meta, out)

        # --- 再处理正文，按标题行推进章节 ---
        lines = [l.strip() for l in (p.get("text") or "").split("\n")]
        for ln in lines:
            if not ln or ln in boiler:
                continue
            if TOC_RE.search(ln):          # 目录行，不进块
                continue
            if PAGENO_RE.match(ln):        # 孤立页码（9/143、- 10 -、纯数字）
                continue
            m = SEC_RE.match(ln)
            if m:
                flush(buf, out, {**meta, "section": section, "subsection": subsection})
                buf = []
                section = m.group(0).split("节")[0] + "节" + (" " + m.group(1).strip() if m.group(1).strip() else "")
                section = section.strip()
                subsection = ""
                meta.update({"section": section, "subsection": subsection})
                buf.append(ln)
                continue
            for rx in (SUB_RE, SUB2_RE):
                m = rx.match(ln)
                if m and not ln.endswith("。"):
                    flush(buf, out, {**meta, "section": section, "subsection": subsection})
                    buf = []
                    subsection = ln[:30]
                    meta.update({"subsection": subsection})
                    buf.append(ln)
                    break
            else:
                buf.append(ln)
        tbl_ctx = "\n".join(buf[-3:])   # 给下一页首个表格当上下文

    flush(buf, out, {"code": code, "name": name, "page": pages[-1]["page"],
                     "printed_page": None, "section": section, "subsection": subsection})
    return out


def main() -> int:
    manifest = json.loads((ROOT / "data" / "manifest.json").read_text(encoding="utf-8"))
    stats = []
    with open(OUT, "w", encoding="utf-8") as f:
        for k, m in enumerate(manifest):
            chunks = chunk_one(m["code"], m["name"])
            for i, c in enumerate(chunks):
                c["chunk_id"] = f"{m['code']}-{i:05d}"
                f.write(json.dumps(c, ensure_ascii=False) + "\n")
            n_tbl = sum(1 for c in chunks if c["kind"] == "table")
            stats.append({"code": m["code"], "name": m["name"], "chunks": len(chunks),
                          "text": len(chunks) - n_tbl, "table": n_tbl})
            print(f"  {m['code']} {m['name']}: {len(chunks)} 块（正文 {len(chunks)-n_tbl} / 表格 {n_tbl}）")
    (ROOT / "data" / "切块统计.json").write_text(
        json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    tot = sum(s["chunks"] for s in stats)
    print(f"\n合计 {tot} 块 → {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
