#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把本地 BM25 索引打包成「浏览器能直接 fetch」的静态资源。

为什么要有这一份：
  Vercel 的 Python 函数在本项目里始终构建失败（项目 dashboard 的 Framework Preset
  会覆盖 vercel.json，旧的个人主页项目还在按旧框架构建）。与其在 dashboard 里猜，
  不如把检索整个搬到浏览器：静态托管零构建，必定能上线，别人也一定能打开。

体积怎么压下来（关键取舍）：
  正文 462 万字 ≈ 13.5MB UTF-8，一股脑塞进首屏，手机流量要等十几秒。
  但打分只需要倒排表，正文只在展示 top-k 时才用得上。所以拆成三段：
    · index.bin.gz  倒排 + 词表 + idf + 块长（首屏必取，gzip 后约 2MB）
    · meta.json     每块的公司/章节/页码（首屏必取，约 200KB）
    · text/<code>.json  某一家公司的正文，按公司切成 15 片，命中哪家取哪家
  单公司题只拉 1 片（几百 KB），跨公司全景题才需要拉全部——那本来就得全看。

一致性怎么保证：
  评分公式、先验权重、分层规则全部照抄 lib/retriever.py，且前端版要在 Node 里
  跑同一批 15 题（10_Vercel/评估_前端版.js），得分不能低于 Python 版。
"""
from __future__ import annotations

import gzip
import json
import pickle
import struct
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "assets"

MAGIC = b"BJBM25\x00\x00"
VERSION = 1
HEADER_FMT = "<8sIIIdddIIIII"  # magic, version, N, vocabCount, avgdl, k1, b, 5 个段偏移
HEADER_LEN = struct.calcsize(HEADER_FMT)  # 68


def varint(v: int) -> bytes:
    out = bytearray()
    while True:
        b = v & 0x7F
        v >>= 7
        if v:
            out.append(b | 0x80)
        else:
            out.append(b)
            break
    return bytes(out)


def pad4(b: bytes) -> bytes:
    """段之间补到 4 字节对齐——JS 的 Uint32Array/Float32Array 要求 byteOffset 是 4 的倍数，
    不对齐会在浏览器里直接抛 RangeError，而且只在真机上才暴露。"""
    r = len(b) % 4
    return b + b"\x00" * (4 - r) if r else b


def build_index(bm: dict) -> bytes:
    vocab = bm["vocab"]
    start, pdoc, ptf = bm["start"], bm["pdoc"], bm["ptf"]
    idf, doc_len = bm["idf"], bm["doc_len"]
    N, avgdl = bm["N"], bm["avgdl"]

    assert max(doc_len) < 65536, "块长超过 u16，需要换格式"
    doc_len_blob = struct.pack(f"<{N}H", *[int(x) for x in doc_len])

    vocab_blob = bytearray()
    for t in vocab:
        raw = t.encode("utf-8")
        vocab_blob += struct.pack("<H", len(raw)) + raw

    idf_blob = struct.pack(f"<{len(vocab)}f", *[float(x) for x in idf])

    # 注意：源索引里的 start 是「倒排项下标」，而这里写的是 varint 字节流，
    # 两者不是一回事。必须按实际写入的字节数重算一套偏移，否则前端解码出垃圾 doc id。
    byte_start = [0] * (len(vocab) + 1)
    post = bytearray()
    for k in range(len(vocab)):
        byte_start[k] = len(post)
        prev = -1
        for p in range(start[k], start[k + 1]):
            d = pdoc[p]
            assert d > prev, "postings 必须按 doc 升序，delta 编码依赖这一点"
            post += varint(d - prev) + varint(ptf[p])
            prev = d
    byte_start[len(vocab)] = len(post)
    start_blob = struct.pack(f"<{len(vocab) + 1}I", *byte_start)

    header_len = HEADER_LEN
    off_doclen = header_len
    off_vocab = off_doclen + len(pad4(doc_len_blob))
    off_idf = off_vocab + len(pad4(vocab_blob))
    off_start = off_idf + len(pad4(idf_blob))
    off_post = off_start + len(pad4(start_blob))
    total = off_post + len(post)

    header = struct.pack(
        HEADER_FMT, MAGIC, VERSION, N, len(vocab), float(avgdl),
        bm.get("k1", 1.5), bm.get("b", 0.75),
        off_doclen, off_vocab, off_idf, off_start, off_post,
    )
    assert len(header) == HEADER_LEN, len(header)
    body = (pad4(doc_len_blob) + pad4(bytes(vocab_blob))
            + pad4(idf_blob) + pad4(start_blob) + bytes(post))
    assert len(header) + len(body) == total, (len(header) + len(body), total)
    return bytes(header) + body


def main() -> None:
    import importlib.util

    OUT.mkdir(parents=True, exist_ok=True)
    with gzip.open(ROOT / "data" / "chunks.pkl.gz", "rb") as f:
        chunks = pickle.load(f)
    with gzip.open(ROOT / "data" / "bm25.pkl.gz", "rb") as f:
        bm = pickle.load(f)

    # 常量从这里取，不另抄一份（公司别名/章节词/先验权重）
    spec2 = importlib.util.spec_from_file_location("rt", ROOT / "lib" / "retriever.py")
    rt = importlib.util.module_from_spec(spec2)
    assert spec2.loader is not None
    spec2.loader.exec_module(rt)

    # ---------- 倒排（二进制 + gzip）----------
    raw = build_index(bm)
    blob = gzip.compress(raw, compresslevel=9, mtime=0)
    (OUT / "index.bin.gz").write_bytes(blob)
    print(f"index.bin.gz  {len(raw)/1e6:.2f}MB → {len(blob)/1e6:.2f}MB")

    # ---------- 元数据 ----------
    codes, names, secs, kinds = [], [], [], []
    sec_idx: dict[str, int] = {}
    items = []
    ordinal: dict[str, int] = {}
    for c in chunks:
        code = c["code"]
        if code not in codes:
            codes.append(code)
            names.append(c["name"])
        ordinal[code] = ordinal.get(code, 0)
        for s in (c["section"], c["subsection"]):
            if s and s not in sec_idx:
                sec_idx[s] = len(secs)
                secs.append(s)
        if c["kind"] not in kinds:
            kinds.append(c["kind"])
        items.append([
            codes.index(code),
            ordinal[code],
            int(c["page"]),
            sec_idx.get(c["section"], -1),
            sec_idx.get(c["subsection"], -1),
            kinds.index(c["kind"]),
        ])
        ordinal[code] += 1

    # 分层召回有个「关键章节保底」：每家至少塞一块含「主要会计数据/财务指标」的块。
    # Python 版查的是正文文本，但前端正文是按需加载的，检索时还没拉到——
    # 所以这里预先算好哪些块命中，存成下标数组，前端不用碰正文就能做同样的判断。
    key_sec = [i for i, c in enumerate(chunks)
               if any(k in c["text"] for k in rt.KEY_SECTION)]

    meta = {"codes": codes, "names": names, "secs": secs, "kinds": kinds,
            "items": items, "keySec": key_sec}
    (OUT / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"meta.json     {(OUT/'meta.json').stat().st_size/1e3:.0f}KB（{len(items)} 块）")

    # ---------- 分词词典 ----------
    # 前端没有 jieba，只靠索引词表做最大匹配会有个具体后果：
    # 「多少」不在年报词表里（年报里不写"多少"），就被切成「多」+「少」两个高频字，
    # 它们的 df 极大，会往里掺噪声——Q6（山西汾酒营收）正是这么丢的。
    # 所以把 jieba 的高频词形也导出一份：只取词形、按词频截断，gzip 后不到 300KB。
    dict_p = ROOT / "tmp" / "site" / "jieba" / "dict.txt"
    if dict_p.exists():
        words = []
        for line in dict_p.read_text(encoding="utf-8").split("\n"):
            parts = line.split()
            if len(parts) >= 2 and parts[1].isdigit():
                w = parts[0]
                if 1 < len(w) <= 8:
                    words.append((int(parts[1]), w))
        words.sort(reverse=True)
        keep = [w for _, w in words[:120000]]
        blob = gzip.compress("\n".join(keep).encode("utf-8"), compresslevel=9, mtime=0)
        (OUT / "dict.txt.gz").write_bytes(blob)
        print(f"dict.txt.gz   {len(keep)} 词，{len(blob)/1e3:.0f}KB（jieba 高频词形，供前端分词）")

    # ---------- 正文按公司分片 ----------
    tdir = OUT / "text"
    tdir.mkdir(exist_ok=True)
    total = 0
    for ci, code in enumerate(codes):
        texts = [c["text"] for c in chunks if c["code"] == code]
        p = tdir / f"{code}.json"
        p.write_text(json.dumps({"t": texts}, ensure_ascii=False, separators=(",", ":")),
                     encoding="utf-8")
        total += p.stat().st_size
    print(f"text/*.json   {len(codes)} 片，共 {total/1e6:.2f}MB（按需拉取）")

    # ---------- 15 题判据导出（给 Node 版评估用，避免两处各写一份题目）----------
    spec = importlib.util.spec_from_file_location("q15", ROOT / "05_评估" / "评估_15题.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    qs = [{"id": q["id"], "group": q["group"], "q": q["q"], "gold": q["gold"],
           "answer": q["answer"]} for q in mod.QUESTIONS]
    p = ROOT / "10_Vercel" / "题库_15.json"
    p.write_text(json.dumps(qs, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"题库_15.json  {len(qs)} 题")

    # ---------- 常量导出：前端与 Python 同源 ----------
    # 公司别名 / 章节词 / 先验权重这些常量一旦两边各写一份，迟早漂移，
    # 而漂移的表现是"线上召回和本地对不上"，很难查。所以直接从模块里取。
    cfg = {
        "companyAlias": rt.COMPANY_ALIAS,
        "sectionHint": rt.SECTION_HINT,
        "keySection": rt.KEY_SECTION,
        "k1": rt.K1, "b": rt.B, "rrfK": rt.RRF_K,
        "wCompany": rt.W_COMPANY, "wSection": rt.W_SECTION,
        "maxWordLen": max(len(t) for t in bm["vocab"]),
    }
    (OUT / "config.json").write_text(
        json.dumps(cfg, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    stats_p = ROOT / "data" / "stats.json"
    if stats_p.exists():
        (OUT / "stats.json").write_bytes(stats_p.read_bytes())
    print(f"config.json   {len(cfg['companyAlias'])} 家公司别名，最长词 {cfg['maxWordLen']} 字")


if __name__ == "__main__":
    main()
