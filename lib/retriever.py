#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
线上检索核心（Vercel Serverless 版）—— 纯 Python BM25，零 ML 依赖。

为什么要另写一份：
  Vercel 的 Serverless Function 有 250MB 包体上限，装不下 torch/sentence-transformers
  （光 CPU 版 torch 就 180MB+）。所以线上走纯 BM25。这不是降级的妥协——离线评估里
  纯 BM25 单独跑 15 题是 8/15，bge 混合是 9/15，只差 1 题（见 05_评估/结果_原始.json）。

一致性保证（重要）：
  评分公式、分词、元数据先验、分层召回的每一步都与 03_索引/检索.py 逐字对齐，
  并由 10_Vercel/自检_与本地一致.py 用 15 题比对 top-8 召回块，必须逐条全等。

  idf 用的是 rank_bm25.BM25Okapi 的那套（不是教科书常见的 1+(...) 版）：
      idf = ln(N - df + 0.5) - ln(df + 0.5)
      负 idf（词出现在半数以上文档里）裁剪为 eps = 0.25 × 平均 idf
  这两条照抄 tmp/site/rank_bm25.py:85-103，改任一条排序就会跑偏，自检会抓到。

  打分 = BM25(k1=1.5, b=0.75) → 名次 → RRF 1/(60+rank+1) → 加元数据先验
  先验：命中问题里的公司 +0.012，命中章节意图 +0.006
"""
from __future__ import annotations

import gzip
import pickle
import re
from array import array
from pathlib import Path

# 数据放部署根目录的 data/（不是 api/data/）——api/ 下每个 .py 都会被 Vercel
# 当成独立函数入口，数据不能混在那儿
DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# ---------------------------------------------------------------- 领域词典
# 与 03_索引/检索.py 完全同源：不让 jieba 把公司名和财报科目切碎
USER_WORDS = [
    "贵州茅台", "五粮液", "泸州老窖", "山西汾酒", "洋河股份", "古井贡酒", "今世缘",
    "酒鬼酒", "水井坊", "迎驾贡酒", "口子窖", "老白干酒", "天佑德酒", "金徽酒", "顺鑫农业",
    "营业收入", "营业总收入", "营业成本", "归母净利润", "扣非净利润", "毛利率", "净利率",
    "销售费用", "管理费用", "研发费用", "财务费用", "合同负债", "预收款项", "存货",
    "经营活动产生的现金流量净额", "净资产收益率", "加权平均净资产收益率", "每股收益",
    "总资产", "所有者权益", "资产负债率", "经销商", "直销", "批发代理", "吨价",
    "基酒", "产能", "销量", "库存量", "生产量", "销售量", "分红", "派息", "现金分红",
    "半成品", "成品酒", "系列酒", "中高档酒", "普通酒",
]

COMPANY_ALIAS = {
    "600519": ["贵州茅台", "茅台", "maotai"],
    "000858": ["五粮液", "wuliangye"],
    "000568": ["泸州老窖", "老窖"],
    "600809": ["山西汾酒", "汾酒"],
    "002304": ["洋河股份", "洋河"],
    "000596": ["古井贡酒", "古井"],
    "603369": ["今世缘"],
    "000799": ["酒鬼酒"],
    "600779": ["水井坊"],
    "603198": ["迎驾贡酒", "迎驾"],
    "603589": ["口子窖"],
    "600559": ["老白干酒", "老白干", "衡水老白干"],
    "002646": ["天佑德酒", "天佑德", "青青稞酒"],
    "603919": ["金徽酒"],
    "000860": ["顺鑫农业", "顺鑫", "牛栏山"],
}

SECTION_HINT = {
    "毛利率": ["利润率", "主营业务分析", "营业收入", "分行业", "经营情况"],
    "分红": ["利润分配", "现金分红", "普通股股利"],
    "现金流": ["现金流量", "经营活动"],
    "经销商": ["经销商", "销售模式", "批发代理", "直销"],
    "产能": ["产能", "生产量", "销售量", "库存量", "产销"],
    "股东": ["股东", "股份变动"],
    "研发": ["研发", "开发支出"],
    "员工": ["员工", "在职员工"],
    "子公司": ["子公司", "合并范围"],
    "审计": ["审计", "会计师事务所"],
}

KEY_SECTION = ["主要会计数据", "主要财务指标", "会计数据和财务指标"]

K1, B = 1.5, 0.75
RRF_K = 60
W_COMPANY = 0.012
W_SECTION = 0.006


# ---------------------------------------------------------------- 分词
_JIEBA = None


def tokenize(text: str) -> list[str]:
    global _JIEBA
    if _JIEBA is None:
        import jieba
        for w in USER_WORDS:
            jieba.add_word(w)
        # jieba 首次调用会加载词典（~1s），预热一次避免首次查询明显卡顿
        jieba.lcut("预热")
        _JIEBA = jieba
    return [t for t in _JIEBA.lcut(text) if t.strip()]


def detect_company(query: str) -> str | None:
    for code, aliases in COMPANY_ALIAS.items():
        for a in aliases:
            if a in query:
                return code
    m = re.search(r"\b(\d{6})\b", query)
    return m.group(1) if m else None


def detect_companies(query: str) -> list[str]:
    """问题里点名了几家公司？（按代码去重，别名子串重叠不会重复计数）"""
    out = []
    for code, aliases in COMPANY_ALIAS.items():
        if any(a in query for a in aliases):
            out.append(code)
    return out


def detect_section_hint(query: str) -> list[str]:
    out = []
    for kw, secs in SECTION_HINT.items():
        if kw in query:
            out += secs
    return out


def index_text(c: dict) -> str:
    """索引文本：公司名 + 章节 + 正文。与离线索引同一构造，不可改。"""
    return f"{c['name']} {c['section']} {c['subsection']} {c['text']}"


# ---------------------------------------------------------------- BM25
class BM25:
    """rank_bm25.BM25Okapi 的等价纯 Python 实现。

    倒排表压成三个扁平 array（pdoc / ptf / start），加载即从 buffer 还原，
    比 pickle 几百万个小 tuple 快一个数量级——Serverless 冷启动要的就是这个。
    """

    __slots__ = ("vocab", "vocab_index", "start", "pdoc", "ptf", "idf", "doc_len", "avgdl", "N")

    def __init__(self, d: dict):
        self.vocab = d["vocab"]
        self.vocab_index = {t: i for i, t in enumerate(self.vocab)}
        self.start = d["start"]
        self.pdoc = d["pdoc"]
        self.ptf = d["ptf"]
        self.idf = d["idf"]
        self.doc_len = d["doc_len"]
        self.avgdl = d["avgdl"]
        self.N = d["N"]

    @classmethod
    def load(cls, path: Path) -> "BM25":
        with gzip.open(path, "rb") as f:
            return cls(pickle.load(f))

    def get_scores(self, qtokens: list[str]) -> dict[int, float]:
        """只返回非零分的文档；未命中的文档分 0，在 order() 里按索引顺序接在后面。"""
        vi = self.vocab_index
        scores: dict[int, float] = {}
        start, pdoc, ptf, idf = self.start, self.pdoc, self.ptf, self.idf
        doc_len, avgdl = self.doc_len, self.avgdl
        for t in qtokens:
            k = vi.get(t)
            if k is None:
                continue
            w = idf[k]
            for p in range(start[k], start[k + 1]):
                i = pdoc[p]
                tf = ptf[p]
                scores[i] = scores.get(i, 0.0) + w * (tf * (K1 + 1.0)) / (
                    tf + K1 * (1.0 - B + B * doc_len[i] / avgdl))
        return scores

    def order(self, qtokens: list[str], cand: list[int] | None = None) -> list[int]:
        """返回候选集按 BM25 分数降序的名次表（含 0 分文档，保持与离线一致的 rank）。"""
        scores = self.get_scores(qtokens)
        if cand is None:
            hit = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
            hit_idx = [i for i, _ in hit]
            seen = set(hit_idx)
            rest = [i for i in range(self.N) if i not in seen]
            return hit_idx + rest
        # 限定某家公司时只有 cand 参与排序，0 分的也留在表里占位
        in_cand = [(i, s) for i, s in scores.items() if i in set(cand)]
        hit_idx = [i for i, _ in sorted(in_cand, key=lambda kv: (-kv[1], kv[0]))]
        seen = set(hit_idx)
        rest = [i for i in cand if i not in seen]
        return hit_idx + rest


# ---------------------------------------------------------------- 检索器
class Retriever:
    def __init__(self, chunks: list[dict], bm25: BM25, stats: dict):
        self.chunks = chunks
        self.bm25 = bm25
        self.stats = stats
        self.codes = sorted({c["code"] for c in chunks})
        self._by_code: dict[str, list[int]] = {}
        for i, c in enumerate(chunks):
            self._by_code.setdefault(c["code"], []).append(i)

    @classmethod
    def load(cls, data_dir: Path = DATA_DIR) -> "Retriever":
        with gzip.open(data_dir / "chunks.pkl.gz", "rb") as f:
            chunks = pickle.load(f)
        bm25 = BM25.load(data_dir / "bm25.pkl.gz")
        stats = {}
        p = data_dir / "stats.json"
        if p.exists():
            import json
            stats = json.loads(p.read_text(encoding="utf-8"))
        return cls(chunks, bm25, stats)

    # ---------- 单次检索 ----------
    def search(self, query: str, topk: int = 8, code: str | None = None) -> list[dict]:
        cand = self._by_code.get(code, []) if code else list(range(len(self.chunks)))
        order = self.bm25.order(tokenize(query), cand)
        q_code = code or detect_company(query)
        q_secs = detect_section_hint(query)

        hits = []
        # RRF：BM25 单路，分数 1/(K+rank+1)；再加元数据先验
        for r, i in enumerate(order[:len(cand)]):
            e = {
                "i": i,
                "score": 1.0 / (RRF_K + r + 1),
                "rrf": 1.0 / (RRF_K + r + 1),
                "bm25_rank": r + 1,
            }
            c = self.chunks[i]
            bonus = 0.0
            if q_code and c["code"] == q_code:
                bonus += W_COMPANY
            if q_secs and any(s in (c["section"] + c["subsection"]) for s in q_secs):
                bonus += W_SECTION
            e["score"] += bonus
            e["rrf"] += bonus
            e["bonus"] = bonus
            e["chunk"] = c
            hits.append(e)

        hits.sort(key=lambda h: (-h["score"], h["i"]))
        out = hits[:topk]
        for k, h in enumerate(out, 1):
            h["rank"] = k
            h["by"] = "bm25"
        return out

    # ---------- 分层召回（跨公司题的修法）----------
    def stratified(self, query: str, per_company: int = 2, cap: int = 45,
                   key_section: bool = True) -> list[dict]:
        """15 家公司每家各取 top-N，再给每家保底塞一块「主要会计数据/财务指标」章节。

        为什么必须分层：全局 top-k 的名额会被和问句最像的那一两家垄断，
        跨公司全景题（"15 家里谁增长最快"）前 8 块只能覆盖 1-6 家。
        离线评估里这是唯一能把 Q7 / Q10 / Q12 救回来的策略（见 05_评估/对比_召回策略.md）。
        """
        pooled: list[dict] = []
        for code in self.codes:
            pooled += self.search(query, topk=per_company, code=code)
            if key_section:
                deep = self.search(query, topk=60, code=code)
                sec = [h for h in deep if any(k in h["chunk"]["text"] for k in KEY_SECTION)]
                if sec:
                    pooled.append(sec[0])
        pooled.sort(key=lambda h: -h["score"])
        seen, out = set(), []
        for h in pooled:
            c = h["chunk"]
            key = (c["code"], c["page"], c["chunk_id"])
            if key in seen:
                continue
            seen.add(key)
            out.append(h)
        out = out[:cap]
        for k, h in enumerate(out, 1):
            h["rank"] = k
        return out


    # ---------- 自适应：按问题里有没有点名公司来选策略 ----------
    def auto(self, query: str, topk: int = 8) -> list[dict]:
        """点名了公司 → 全局 top-k；没点名（"15 家里谁最高"）→ 分层召回。

        为什么不能一刀切用分层：分层是给跨公司题补覆盖度的，但单公司题里
        它会把另外 14 家的块硬塞进来，把真正要找的那家挤下去——实测
        "贵州茅台2025年营业总收入是多少" 在分层下前 3 块全是别家。
        点名两家以上（"茅台和五粮液谁毛利率高"）时又必须分层，否则名额
        会被第一家垄断。

        15 题实测（10_Vercel/评估_线上策略.py，纯 BM25 口径）：
          全局 8/15 ｜ 一刀切分层 3/15 ｜ 自适应 9/15
        自适应这一档与本地 bge 神经 embedding 的混合检索持平。
        """
        n = len(detect_companies(query))
        if n == 1:                      # 只点名一家：公司先验已经足够，分层反而稀释
            return self.search(query, topk=topk)
        return self.stratified(query, per_company=2, cap=45)


_CACHE: Retriever | None = None


def get() -> Retriever:
    """懒加载：Serverless 冷启动只在第一次请求时付加载成本。"""
    global _CACHE
    if _CACHE is None:
        _CACHE = Retriever.load()
    return _CACHE
