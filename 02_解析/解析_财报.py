#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
把年报 PDF 解析成「页级」JSONL：正文文字 + 表格（按行列还原）。

产出 data/pages/{代码}.jsonl，每行一页：
    {"code","name","page","text","tables":[{"n_rows","n_cols","md","rows"}],"n_chars"}

表格用 pdfplumber 基于线条/文本对齐检测，还原成 markdown 表（保留行列结构），
同时保留 rows 二维数组，方便后面做数值类问答。

用法：
    python 02_解析/解析_财报.py            # 全量（多进程）
    python 02_解析/解析_财报.py --one 600519  # 只解析一家，先看效果
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from multiprocessing import Pool
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PAGES_DIR = ROOT / "data" / "pages"
PAGES_DIR.mkdir(parents=True, exist_ok=True)
PDF_DIR = ROOT / "data" / "pdf"


def clean(s: str | None) -> str:
    if s is None:
        return ""
    # PDF 里常见换行/空白噪声；表格单元格内换行统一成空格
    return re.sub(r"\s+", " ", str(s)).strip()


def rows_to_md(rows: list[list[str]]) -> str:
    if not rows:
        return ""
    head = [clean(c) for c in rows[0]]
    out = ["| " + " | ".join(head) + " |",
           "|" + "|".join(["---"] * len(head)) + "|"]
    for r in rows[1:]:
        cells = [clean(c) for c in r]
        if len(cells) < len(head):
            cells += [""] * (len(head) - len(cells))
        out.append("| " + " | ".join(cells[:len(head)]) + " |")
    return "\n".join(out)


def parse_one(item: dict) -> dict:
    """解析一份 PDF，返回统计信息。"""
    import pdfplumber  # 子进程内导入，避免主进程开销

    pdf_path = ROOT / item["pdf"]
    out_path = PAGES_DIR / f"{item['code']}.jsonl"
    t0 = time.time()
    n_pages = n_tables = n_chars = 0

    with pdfplumber.open(str(pdf_path)) as pdf, open(out_path, "w", encoding="utf-8") as f:
        for i, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            tables = []
            try:
                for tbl in (page.find_tables() or []):
                    rows = tbl.extract() or []
                    rows = [[clean(c) for c in r] for r in rows if r]
                    # 丢掉几乎全空的表格（财报里线条误检很常见）
                    filled = sum(1 for r in rows for c in r if c)
                    if len(rows) < 2 or len(rows[0]) < 2 or filled < 4:
                        continue
                    tables.append({
                        "n_rows": len(rows), "n_cols": len(rows[0]),
                        "md": rows_to_md(rows), "rows": rows,
                    })
            except Exception:  # noqa: BLE001  某些页表格检测会抛错，跳过即可
                pass

            rec = {
                "code": item["code"], "name": item["name"], "page": i,
                "text": text, "tables": tables, "n_chars": len(text),
            }
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            n_pages += 1
            n_tables += len(tables)
            n_chars += len(text)

    return {"code": item["code"], "name": item["name"], "pages": n_pages,
            "tables": n_tables, "chars": n_chars, "secs": round(time.time() - t0, 1)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--one", help="只解析指定代码")
    ap.add_argument("--procs", type=int, default=6)
    args = ap.parse_args()

    manifest = json.loads((ROOT / "data" / "manifest.json").read_text(encoding="utf-8"))
    if args.one:
        manifest = [m for m in manifest if m["code"] == args.one]

    t0 = time.time()
    stats = []
    if len(manifest) == 1:
        stats.append(parse_one(manifest[0]))
    else:
        with Pool(args.procs) as pool:
            for r in pool.imap_unordered(parse_one, manifest):
                stats.append(r)
                print(f"  完成 {r['code']} {r['name']}: {r['pages']}页 / {r['tables']}表 / "
                      f"{r['chars']:,}字 / {r['secs']}s", flush=True)

    stats.sort(key=lambda x: x["code"])
    (ROOT / "data" / "解析统计.json").write_text(
        json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n合计 {sum(s['pages'] for s in stats)} 页 / {sum(s['tables'] for s in stats)} 个表格 / "
          f"{sum(s['chars'] for s in stats):,} 字符，用时 {time.time()-t0:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
