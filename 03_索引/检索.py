#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
混合检索：BM25（稀疏，字面匹配） + 稠密向量（语义），RRF 融合。

向量后端自动选择：
  1) 装了 sentence-transformers → BAAI/bge-small-zh-v1.5 神经 embedding
  2) 否则 → TF-IDF（字符 n-gram）经 SVD 压成 256 维稠密向量
两种都真实可用；后者零依赖、纯离线、任何人 clone 都能跑。

对外接口：
    idx = Index.load(ROOT)
    hits = idx.search("五粮液2025年毛利率是多少", topk=8, mode="hybrid")
"""
from __future__ import annotations

import json
import pickle
import re
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
IDX_DIR = ROOT / "data" / "index"
BGE_MODEL = "BAAI/bge-small-zh-v1.5"

# 财报领域词：不让 jieba 把它们切碎
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


def _tokenize(text: str) -> list[str]:
    import jieba
    return [t for t in jieba.lcut(text) if t.strip()]


# ---------------------------------------------------------------- 语义增强文本
# 表格块几乎只有数字，直接编码出来的向量很"空"，跟问句对不上。
# 给它拼一段上下文前缀（公司 / 报告期 / 章节路径 / 表头），让向量有语义锚点。
# 展示给用户时仍用原始 text，embed_text 只喂给编码器。
def embed_text(c: dict, head: int = 160) -> str:
    meta = f"{c['name']}（{c['code']}）年报 {c.get('section','')} {c.get('subsection','')}".strip()
    if c["kind"] == "table":
        # 表头/前几行往往含"项目 / 2025年度 / 2024年度"这类关键列，带上
        return f"{meta} 财务报表表格 {c['text'][:head]}"
    return f"{meta} {c['text'][:head]}"


# ---------------------------------------------------------------- 查询侧先验
# 公司别名 -> 代码（用于"公司先验加分"，不做硬过滤，硬过滤会漏掉跨公司题）
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

# 意图关键词 -> 章节先验（命中则给对应章节的块加分）
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


def detect_company(query: str) -> str | None:
    for code, aliases in COMPANY_ALIAS.items():
        for a in aliases:
            if a in query:
                return code
    m = re.search(r"\b(\d{6})\b", query)
    return m.group(1) if m else None


def detect_section_hint(query: str) -> list[str]:
    out = []
    for kw, secs in SECTION_HINT.items():
        if kw in query:
            out += secs
    return out


class Index:
    chunks: list[dict]
    tokens: list[list[str]]
    backend: str
    vec: np.ndarray

    # ---------- 构建 ----------
    def __init__(self, chunks: list[dict], backend: str = "auto", tag: str | None = None):
        import jieba
        for w in USER_WORDS:
            jieba.add_word(w)
        self.chunks = chunks
        # 索引文本 = 原文本 + 元数据前缀（公司/章节），BM25 也受益于公司名与章节名
        self.embed = [embed_text(c) for c in chunks]
        self.index_text = [f"{c['name']} {c['section']} {c['subsection']} {c['text']}"
                           for c in chunks]
        self.tokens = [_tokenize(t) for t in self.index_text]
        from rank_bm25 import BM25Okapi
        self.bm25 = BM25Okapi(self.tokens)
        self.backend, self.vec = self._build_vectors(backend)
        self.tag = tag or ("bge" if self.backend.startswith("sbert") else "tfidf")
        self.dir = IDX_DIR / self.tag

    def _build_vectors(self, backend: str):
        if backend in ("auto", "sbert"):
            try:
                from sentence_transformers import SentenceTransformer
                self._sbert = SentenceTransformer(BGE_MODEL)
                emb = self._sbert.encode(
                    self.embed,
                    batch_size=32, normalize_embeddings=True, show_progress_bar=True)
                return f"sbert:{BGE_MODEL}", np.asarray(emb, dtype=np.float32)
            except Exception as e:  # noqa: BLE001
                if backend == "sbert":
                    raise
                print(f"[索引] 神经 embedding 不可用（{type(e).__name__}: {str(e)[:120]}），降级 TF-IDF+SVD")

        from sklearn.decomposition import TruncatedSVD
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.preprocessing import normalize

        tf = TfidfVectorizer(analyzer="char_wb", ngram_range=(1, 2), min_df=2, max_features=200_000)
        self._tfidf_corpus = [" ".join(t) for t in self.tokens]
        X = tf.fit_transform(self._tfidf_corpus)
        n_comp = min(256, min(X.shape) - 1)
        svd = TruncatedSVD(n_components=n_comp, random_state=0)
        Xd = svd.fit_transform(X)
        self._tfidf, self._svd = tf, svd
        return f"tfidf-svd-{n_comp}", normalize(np.asarray(Xd, dtype=np.float32))

    # ---------- query 编码 ----------
    def _encode_query(self, q: str) -> np.ndarray:
        if self.backend.startswith("sbert"):
            if not hasattr(self, "_sbert"):
                from sentence_transformers import SentenceTransformer
                self._sbert = SentenceTransformer(BGE_MODEL)
            return np.asarray(self._sbert.encode([q], normalize_embeddings=True),
                              dtype=np.float32)[0]
        v = self._tfidf.transform([" ".join(_tokenize(q))])
        v = self._svd.transform(v)
        n = float(np.linalg.norm(v))
        return (v / n).astype(np.float32)[0] if n else v.astype(np.float32)[0]

    # ---------- 检索 ----------
    # RRF 常数 K：越小越看重头部排名。60 是常用默认。
    K = 60
    # 两路的权重：字面精确匹配（数字/科目名）信 BM25 多一点，语义泛化信向量
    W = {"bm25": 1.0, "vector": 0.9}
    # 元数据先验：公司命中 / 章节命中，直接加到融合分上
    W_COMPANY = 0.012     # ≈ 相当于从 RRF 的第1名掉到第2名的差距量级
    W_SECTION = 0.006

    def search(self, query: str, topk: int = 8, mode: str = "hybrid",
               code: str | None = None) -> list[dict]:
        """mode: hybrid | bm25 | vector；code 可限定某家公司（硬过滤）。"""
        cand = list(range(len(self.chunks)))
        if code:
            cand = [i for i, c in enumerate(self.chunks) if c["code"] == code]

        # pool 必须是常数，不能随 topk 变：否则同一个问题在 topk=2 与 topk=60 下
        # 看到的候选池不同，RRF 分数也不同，分层召回（每家取 top-2）取到的块会不自洽。
        # 这里直接取全部候选（几千块排序可忽略不计），保证排序与 topk 无关。
        pool = len(cand)
        ranks: dict[int, dict] = {}

        def add(source: str, order: list[int]):
            w = self.W[source] if mode == "hybrid" else 1.0
            for r, i in enumerate(order):
                e = ranks.setdefault(i, {"i": i, "score": 0.0, "src": {}})
                e["score"] += w / (self.K + r + 1)
                e["src"][source] = r + 1

        if mode in ("hybrid", "bm25"):
            scores = self.bm25.get_scores(_tokenize(query))
            add("bm25", sorted(cand, key=lambda i: scores[i], reverse=True)[:pool])

        if mode in ("hybrid", "vector"):
            sims = self.vec @ self._encode_query(query)
            add("vector", sorted(cand, key=lambda i: float(sims[i]), reverse=True)[:pool])

        # ---- 元数据先验 ----
        q_code = code or detect_company(query)
        q_secs = detect_section_hint(query)
        for e in ranks.values():
            c = self.chunks[e["i"]]
            bonus = 0.0
            if q_code and c["code"] == q_code:
                bonus += self.W_COMPANY
            if q_secs and any(s in (c["section"] + c["subsection"]) for s in q_secs):
                bonus += self.W_SECTION
            e["score"] += bonus
            e["bonus"] = round(bonus, 4)

        hits = sorted(ranks.values(), key=lambda e: -e["score"])[:topk]
        return [{"chunk": self.chunks[e["i"]], "rrf": round(e["score"], 5),
                 "rank": k + 1, "by": e["src"], "bonus": e.get("bonus", 0.0)}
                for k, e in enumerate(hits)]

    # ---------- 持久化 ----------
    def save(self):
        self.dir.mkdir(parents=True, exist_ok=True)
        np.save(self.dir / "vec.npy", self.vec)
        with open(self.dir / "bm25.pkl", "wb") as f:
            pickle.dump(self.bm25, f)
        extra = {}
        if self.backend.startswith("tfidf"):
            extra = {"vectorizer": self._tfidf, "svd": self._svd}
        with open(self.dir / "tfidf.pkl", "wb") as f:
            pickle.dump(extra, f)
        (self.dir / "meta.json").write_text(json.dumps(
            {"backend": self.backend, "n_chunks": len(self.chunks),
             "model": BGE_MODEL if self.backend.startswith("sbert") else "char_wb(1,2)+SVD"},
            ensure_ascii=False, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, root: Path = ROOT, tag: str = "bge") -> "Index":
        chunks = [json.loads(l) for l in
                  (root / "data" / "chunks.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
        d = IDX_DIR / tag
        meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
        obj = cls.__new__(cls)
        obj.chunks = chunks
        obj.tag, obj.dir = tag, d
        import jieba
        for w in USER_WORDS:
            jieba.add_word(w)
        obj.embed = [embed_text(c) for c in chunks]
        obj.index_text = [f"{c['name']} {c['section']} {c['subsection']} {c['text']}"
                          for c in chunks]
        obj.tokens = [_tokenize(t) for t in obj.index_text]
        with open(d / "bm25.pkl", "rb") as f:
            obj.bm25 = pickle.load(f)
        obj.vec = np.load(d / "vec.npy")
        obj.backend = meta["backend"]
        with open(d / "tfidf.pkl", "rb") as f:
            extra = pickle.load(f)
        if extra:
            obj._tfidf, obj._svd = extra["vectorizer"], extra["svd"]
        elif obj.backend.startswith("sbert"):
            from sentence_transformers import SentenceTransformer
            obj._sbert = SentenceTransformer(BGE_MODEL)
        return obj


def snippet(text: str, n: int = 260) -> str:
    return re.sub(r"\s+", " ", text)[:n]


if __name__ == "__main__":
    import sys
    idx = Index.load()
    print("后端:", idx.backend, "块数:", len(idx.chunks))
    for q in sys.argv[1:] or ["贵州茅台2025年营业收入是多少", "哪家白酒公司毛利率最高"]:
        print("\nQ:", q)
        for h in idx.search(q, topk=3):
            c = h["chunk"]
            print(f"  #{h['rank']} rrf={h['rrf']} {c['name']} P{c['page']} {c['section'][:12]} | "
                  f"{snippet(c['text'], 90)}")
