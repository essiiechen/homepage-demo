#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Vercel Serverless 函数：POST /api/ask -> {answer, hits, stats}

运行时依赖只有一个纯 Python 包 jieba（分词），没有 flask、没有 torch、
没有任何模型权重。刻意做成这样是为了把线上部署的失败面压到最小：
  · Vercel 函数包体上限 250MB，CPU 版 torch 一个就 180MB+，根本装不进去；
  · 连 flask 都不用，是因为 WSGI 用标准库就能满足，少一个装包环节少一处风险。
线上因此是纯 BM25：离线 15 题评估里纯 BM25 + 自适应召回 9/15，
与本地 bge 神经 embedding 混合检索持平（见 05_评估/，脚本可复现）。

冷启动：数据预构建成 gzip 倒排表（10_Vercel/预构建_索引.py），加载只做解压+反序列化。

本地调试： python3 api/ask.py   → http://127.0.0.1:8766
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

import answer as A          # noqa: E402
import retriever as R       # noqa: E402

JSON_HEADERS = [("Content-Type", "application/json; charset=utf-8"),
                ("Access-Control-Allow-Origin", "*"),
                ("Access-Control-Allow-Headers", "Content-Type, Authorization"),
                ("Access-Control-Allow-Methods", "GET, POST, OPTIONS")]


def highlight(text: str, query: str) -> str:
    """给命中的关键词套 <mark>，前端展示用。"""
    terms = re.findall(r"[\u4e00-\u9fa5A-Za-z0-9]{2,}", query)
    out = text
    for t in sorted(set(terms), key=len, reverse=True)[:8]:
        out = re.sub(re.escape(t), lambda m: f"\x01{m.group(0)}\x02", out)
    return (out.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace("\x01", '<mark class="hl">').replace("\x02", "</mark>"))


# ------------------------------------------------------------------------ 业务
def do_ask(d: dict) -> dict:
    q = (d.get("q") or "").strip()
    if not q:
        return {"error": "空问题"}
    try:
        topk = max(1, min(int(d.get("topk", 8)), 40))
    except (TypeError, ValueError):
        topk = 8
    strategy = d.get("strategy", "auto")
    use_llm = bool(d.get("llm", True))
    code = d.get("code") or None

    t0 = time.time()
    rt = R.get()
    t_load = time.time() - t0

    t1 = time.time()
    # 限定某家公司时分层没有意义（只有一家），直接全局 top-k
    if code:
        hits = rt.search(q, topk=topk, code=code)
    elif strategy == "stratified":
        hits = rt.stratified(q, per_company=2, cap=45)
    elif strategy == "global":
        hits = rt.search(q, topk=topk)
    else:                                   # auto：按问题点名了几家公司自动分流
        hits = rt.auto(q, topk=topk)
    t_ret = time.time() - t1

    res = {"ok": False, "text": "", "model": None, "error": None}
    if use_llm and A.has_key():
        res = A.answer_llm(q, hits)
    if not res["ok"]:
        res = {"ok": True, "text": A.answer_extractive(q, hits),
               "model": "extractive（年报原文摘录，未调用大模型）", "error": res.get("error")}

    payload_hits = []
    for h in hits[:topk]:
        c = h["chunk"]
        payload_hits.append({
            "rank": h["rank"], "score": round(h["score"], 5),
            "by": h.get("bm25_rank", h.get("by", "-")),
            "code": c["code"], "name": c["name"], "page": c["page"],
            "printed_page": c.get("printed_page"), "section": c["section"],
            "subsection": c["subsection"], "kind": c["kind"],
            "text": c["text"], "html": highlight(c["text"][:1600], q),
            "n_chars": c["n_chars"],
        })

    return {
        "q": q, "strategy": strategy, "topk": topk,
        "n_companies": len({h["chunk"]["code"] for h in hits}),
        "answer": res["text"], "model": res["model"], "llm_ok": res["ok"],
        "llm_error": res["error"],
        "cites": A.cites(res["text"]) if res.get("ok") else [],
        "hits": payload_hits,
        "stats": {
            "retrieval_ms": round(t_ret * 1000),
            "load_ms": round(t_load * 1000),
            "total_ms": round((time.time() - t0) * 1000),
            "backend": "bm25（纯 Python，无模型权重）",
            "n_chunks": len(rt.chunks),
            "has_key": A.has_key(),
        },
    }


def do_health() -> dict:
    t = time.time()
    rt = R.get()
    return {"ok": True, "n_chunks": len(rt.chunks),
            "load_ms": round((time.time() - t) * 1000), "stats": rt.stats}


# ------------------------------------------------------------------------ WSGI
def _send(start_response, status, body: bytes, headers):
    start_response(status, headers + [("Content-Length", str(len(body)))])
    return [body]


def _json(start_response, status, obj):
    return _send(start_response, status,
                 json.dumps(obj, ensure_ascii=False).encode("utf-8"), JSON_HEADERS)


def app(environ, start_response):
    """WSGI 入口。Vercel 的 Python runtime 认这个 app；本地也能直接跑。"""
    path = (environ.get("PATH_INFO") or "/").rstrip("/") or "/"
    method = (environ.get("REQUEST_METHOD") or "GET").upper()

    if method == "OPTIONS":                       # CORS 预检
        return _send(start_response, "204 No Content", b"", JSON_HEADERS)

    if path in ("/api/health", "/health"):
        try:
            return _json(start_response, "200 OK", do_health())
        except Exception as e:                    # 索引问题要能看见，不能只报 500
            return _json(start_response, "500 Internal Server Error",
                         {"ok": False, "error": f"{type(e).__name__}: {e}"})

    if path in ("/api/ask", "/ask", "/") and method == "POST":
        try:
            n = int(environ.get("CONTENT_LENGTH") or 0)
        except ValueError:
            n = 0
        raw = environ["wsgi.input"].read(n) if n else b""
        try:
            d = json.loads(raw.decode("utf-8")) if raw else {}
        except Exception:
            d = {}
        try:
            out = do_ask(d)
            status = "200 OK" if "error" not in out else "400 Bad Request"
            return _json(start_response, status, out)
        except Exception as e:
            return _json(start_response, "500 Internal Server Error",
                         {"error": f"{type(e).__name__}: {e}"})

    # GET / ：线上由 Vercel 静态托管直接返回 index.html，走不到这里；
    # 留着是为了本地一个端口就能看到完整页面，方便对照调试。
    p = ROOT / "index.html"
    if p.exists():
        return _send(start_response, "200 OK", p.read_bytes(),
                     [("Content-Type", "text/html; charset=utf-8")])
    return _send(start_response, "404 Not Found", b"not found",
                 [("Content-Type", "text/plain; charset=utf-8")])


if __name__ == "__main__":
    from wsgiref.simple_server import make_server
    rt = R.get()
    print(f"块 {len(rt.chunks)}｜公司 {len(rt.stats.get('companies', []))}｜"
          f"LLM key {'有' if A.has_key() else '无（走年报原文摘录）'}")
    port = int(os.environ.get("PORT", 8766))
    print(f"http://127.0.0.1:{port}")
    make_server("0.0.0.0", port, app).serve_forever()
