#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
财报问答页面：Flask 后端。

启动： python 04_问答/app.py   然后打开 http://127.0.0.1:8765
接口： GET  /            页面
      POST /api/ask      {q, mode, topk, tag} -> {answer, hits, stats}
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "03_索引"))
sys.path.insert(0, str(ROOT / "04_问答"))

from flask import Flask, jsonify, request, render_template  # noqa: E402
from 检索 import Index  # noqa: E402
import llm  # noqa: E402

app = Flask(__name__)
IDX_CACHE: dict[str, Index] = {}
MANIFEST = json.loads((ROOT / "data" / "manifest.json").read_text(encoding="utf-8"))
COMPANIES = sorted({(m["code"], m["name"]) for m in MANIFEST})

# 切块统计是"每家公司一行"的列表；页面要的是全局汇总
_stat_rows = json.loads((ROOT / "data" / "切块统计.json").read_text(encoding="utf-8"))
STATS = {
    "n_chunks": sum(r["chunks"] for r in _stat_rows),
    "n_text": sum(r["text"] for r in _stat_rows),
    "n_table": sum(r["table"] for r in _stat_rows),
}
_p = ROOT / "data" / "解析统计.json"
if _p.exists():
    _pr = json.loads(_p.read_text(encoding="utf-8"))
    rows = _pr if isinstance(_pr, list) else _pr.get("files", [])
    STATS["n_pages"] = sum(r.get("n_pages", r.get("pages", 0)) for r in rows)
    STATS["n_tables"] = sum(r.get("n_tables", r.get("tables", 0)) for r in rows)
    STATS["n_chars"] = sum(r.get("n_chars", r.get("chars", 0)) for r in rows)


def get_idx(tag: str = "bge") -> Index:
    if tag not in IDX_CACHE:
        IDX_CACHE[tag] = Index.load(tag=tag)
    return IDX_CACHE[tag]


def stratified(idx: Index, q: str, per_company: int = 2, cap: int = 30) -> list[dict]:
    """分层召回：15 家公司每家各取 top-N，保证公司覆盖度。

    跨公司全景题（"哪家最快/谁亏损/谁最高"）用全局 top-k 会翻车——
    语义最像的几块全挤在 2-3 家公司里。分层召回是我在评估里找到的修法。
    """
    codes = sorted({c["code"] for c in idx.chunks})
    pooled: list[dict] = []
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


def highlight(text: str, query: str) -> str:
    """给命中的关键词套 <mark>，用于前端展示召回块。"""
    terms = [t for t in re.findall(r"[\u4e00-\u9fa5A-Za-z0-9]{2,}", query)]
    out = text
    for t in sorted(set(terms), key=len, reverse=True)[:8]:
        out = re.sub(re.escape(t), lambda m: f"\x01{m.group(0)}\x02", out)
    return out.replace("\x01", '<mark class="hl">').replace("\x02", "</mark>")


@app.route("/")
def home():
    return render_template(
        "index.html",
        n_companies=len(COMPANIES),
        companies=COMPANIES,
        n_chunks=STATS.get("n_chunks", 0),
        n_pages=STATS.get("n_pages", 0),
        n_tables=STATS.get("n_tables", 0),
        backend=get_idx().backend,
        has_key=bool(llm.get_key()),
        model=llm.get_model(),
    )


@app.route("/api/ask", methods=["POST"])
def ask():
    d = request.get_json(force=True)
    q = (d.get("q") or "").strip()
    mode = d.get("mode", "hybrid")
    topk = int(d.get("topk", 8))
    tag = d.get("tag", "bge")
    use_llm = bool(d.get("llm", True))
    code = d.get("code") or None
    if not q:
        return jsonify({"error": "空问题"}), 400

    strat = d.get("strategy", "global") == "stratified"

    t0 = time.time()
    idx = get_idx(tag)
    if strat:
        hits = stratified(idx, q)
        for k, h in enumerate(hits, 1):
            h["rank"] = k
    else:
        hits = idx.search(q, topk=topk, mode=mode, code=code)
    t_ret = time.time() - t0

    res = {"ok": False, "text": "", "model": None, "error": None}
    if use_llm:
        res = llm.answer(q, hits)
    t_all = time.time() - t0

    payload_hits = []
    for h in hits:
        c = h["chunk"]
        payload_hits.append({
            "rank": h["rank"], "score": h["rrf"], "by": h["by"], "bonus": h["bonus"],
            "code": c["code"], "name": c["name"], "page": c["page"],
            "printed_page": c.get("printed_page"), "section": c["section"],
            "subsection": c["subsection"], "kind": c["kind"],
            "text": c["text"], "html": highlight(c["text"][:1600], q),
            "n_chars": c["n_chars"],
        })
    return jsonify({
        "q": q, "mode": mode, "topk": topk, "tag": tag, "strategy": "stratified" if strat else "global",
        "n_companies": len({h["chunk"]["code"] for h in hits}),
        "answer": res["text"], "llm_ok": res["ok"], "llm_error": res["error"],
        "model": res["model"], "cites": [list(x) for x in llm.cites(res["text"])],
        "hits": payload_hits,
        "stats": {"retrieval_ms": round(t_ret * 1000), "total_ms": round(t_all * 1000),
                  "backend": idx.backend, "n_chunks": len(idx.chunks)},
    })


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--host", default="127.0.0.1")
    a = ap.parse_args()
    print(f"索引后端 {get_idx().backend}｜块 {len(get_idx().chunks)}｜LLM key {'有' if llm.get_key() else '无'}")
    app.run(host=a.host, port=a.port, debug=False, threaded=True)
