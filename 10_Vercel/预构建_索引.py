#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
离线构建一份扁平化的 BM25 与文本块索引（产物进仓库根 data/）。

这是前端索引的上游步骤：先在这里把 9762 块分词、算好 idf 与文档长度、存成扁平数组，
再由 10_Vercel/构建_前端索引.py 打包成浏览器能直接读的 assets/index.bin.gz。
（早期这步是为 Serverless 冷启动做的，那时候的产物进 api/data/；线上改成纯静态后
目录已统一到根 data/，脚本本身的算法没变。）

产物：
  data/chunks.pkl.gz   9762 块（字段与 data/chunks.jsonl 一致）
  data/bm25.pkl.gz     扁平数组版倒排表 + idf + 文档长度
  data/stats.json      页面顶部要显示的统计数字

用法： PYTHONPATH=tmp/site python3 10_Vercel/预构建_索引.py
"""
from __future__ import annotations

import gzip
import json
import math
import pickle
import sys
import time
from array import array
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tmp" / "site"))          # jieba / rank_bm25
sys.path.insert(0, str(ROOT / "lib"))  # retriever

from retriever import index_text, tokenize  # noqa: E402

OUT = ROOT / "data"
OUT.mkdir(parents=True, exist_ok=True)

t0 = time.time()

chunks = [json.loads(l) for l in (ROOT / "data" / "chunks.jsonl").read_text(
    encoding="utf-8").splitlines() if l.strip()]
print(f"读入 {len(chunks)} 块")

# ---------------------------------------------------------------- BM25 构建
t1 = time.time()
tokens = [tokenize(index_text(c)) for c in chunks]
print(f"分词 {len(chunks)} 块耗时 {time.time() - t1:.1f}s")

N = len(chunks)
doc_len = array("i", (len(t) for t in tokens))
avgdl = sum(doc_len) / N

# 每个词的倒排表：doc_idx -> tf
postings: dict[str, Counter] = {}
for i, toks in enumerate(tokens):
    for t, n in Counter(toks).items():
        postings.setdefault(t, Counter())[i] = n

# 词表按 df 降序排列（纯整理，方便人看；检索走 vocab_index 字典）
vocab = sorted(postings.keys(), key=lambda t: (-len(postings[t]), t))
start = array("i", [0] * (len(vocab) + 1))
pdoc: list[int] = []
ptf: list[int] = []

# idf 必须与 rank_bm25.BM25Okapi._calc_idf 逐字一致，差一点排序就跑偏：
#   1) idf = log(N - df + 0.5) - log(df + 0.5)        —— 不是常见的 1 + (...)
#   2) 出现在半数以上文档里的词 idf 会是负数，被裁剪成 eps = 0.25 * 平均 idf
# 这两条是拿 rank_bm25 源码（tmp/site/rank_bm25.py:85-103）对出来的，别凭记忆改。
raw_idf = {t: math.log(N - len(postings[t]) + 0.5) - math.log(len(postings[t]) + 0.5)
           for t in vocab}
average_idf = sum(raw_idf.values()) / len(raw_idf)
eps = 0.25 * average_idf
print(f"平均 idf {average_idf:.4f}｜负 idf 裁剪为 {eps:.4f}"
      f"（{sum(1 for v in raw_idf.values() if v < 0)} 个高频词被裁剪）")

idf_list: list[float] = []
for k, t in enumerate(vocab):
    idf_list.append(eps if raw_idf[t] < 0 else raw_idf[t])
    for d, tf in sorted(postings[t].items()):
        pdoc.append(d)
        ptf.append(tf)
    start[k + 1] = len(pdoc)

idf = array("f", idf_list)
pdoc_a = array("i", pdoc)
ptf_a = array("i", ptf)

blob = {
    "vocab": vocab, "start": start, "pdoc": pdoc_a, "ptf": ptf_a,
    "idf": idf, "doc_len": doc_len, "avgdl": avgdl, "N": N,
}
print(f"词表 {len(vocab)}｜postings {len(pdoc)}｜avgdl {avgdl:.1f}")

with gzip.open(OUT / "bm25.pkl.gz", "wb") as f:
    pickle.dump(blob, f, protocol=4)
with gzip.open(OUT / "chunks.pkl.gz", "wb") as f:
    pickle.dump(chunks, f, protocol=4)

# ---------------------------------------------------------------- 统计数字
_stat_rows = json.loads((ROOT / "data" / "切块统计.json").read_text(encoding="utf-8"))
stats = {
    "n_companies": len({c["code"] for c in chunks}),
    "n_chunks": len(chunks),
    "n_text": sum(1 for c in chunks if c["kind"] == "text"),
    "n_table": sum(1 for c in chunks if c["kind"] == "table"),
    "companies": sorted({(c["code"], c["name"]) for c in chunks}),
}
_p = ROOT / "data" / "解析统计.json"
if _p.exists():
    _pr = json.loads(_p.read_text(encoding="utf-8"))
    rows = _pr if isinstance(_pr, list) else _pr.get("files", [])
    stats["n_pages"] = sum(r.get("n_pages", r.get("pages", 0)) for r in rows)
    stats["n_tables"] = sum(r.get("n_tables", r.get("tables", 0)) for r in rows)
    stats["n_chars"] = sum(r.get("n_chars", r.get("chars", 0)) for r in rows)
(OUT / "stats.json").write_text(
    json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")

print(f"完成 {time.time() - t0:.1f}s")
for p in sorted(OUT.iterdir()):
    print(f"  {p.name:20s} {p.stat().st_size / 1024 / 1024:.2f} MB")
