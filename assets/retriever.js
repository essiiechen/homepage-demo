/* 检索核心（浏览器 / Node 双端同一份代码）
 *
 * 为什么要单独拆一个文件：
 *   评估脚本（10_Vercel/评估_前端版.js）必须跑"线上真正在跑的那份代码"，
 *   否则评估出来的分数是另一个实现的分，没有意义。所以这里不碰任何 DOM，
 *   浏览器用 <script> 引入拿到全局 BJ，Node 用 require 拿到同一个对象。
 *
 * 与 Python 版 lib/retriever.py 的对应关系（逐条对齐，不可自行"优化"）：
 *   idf 已经预计算在索引里（rank_bm25 那套：ln(N-df+0.5)-ln(df+0.5)，负 idf 裁剪）
 *   打分 = BM25(k1,b) → 名次 → RRF 1/(K+rank+1) → 加公司/章节先验
 *   分层 = 每家各取 top-N + 每家保底一块关键章节
 *   自适应 = 点名一家走全局，否则走分层
 */
(function (root, factory) {
  if (typeof module === 'undefined' || !module.exports) { root.BJ = factory(); }
  else { module.exports = factory(); }
})(typeof self !== 'undefined' ? self : globalThis, function () {
  'use strict';

  function parseIndex(buf) {
    // Node 的 zlib.gunzipSync 给的是 Buffer（Uint8Array），浏览器给的是 ArrayBuffer。
    // DataView 只认后者，这里统一一次，两端才能共用同一份解析代码。
    if (buf instanceof Uint8Array) {
      buf = buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength);
    }
    const dv = new DataView(buf);
    const dec = new TextDecoder('utf-8');
    const magic = dec.decode(new Uint8Array(buf, 0, 6));
    if (magic !== 'BJBM25') throw new Error('索引格式不对（magic=' + magic + '）');
    const version = dv.getUint32(8, true);
    if (version !== 1) throw new Error('索引版本不支持：' + version);

    // 布局照 struct '<8sIIIdddIIIII'（'<' 无对齐填充，偏移紧挨着）
    const N = dv.getUint32(12, true);
    const V = dv.getUint32(16, true);
    const avgdl = dv.getFloat64(20, true);
    const k1 = dv.getFloat64(28, true);
    const b = dv.getFloat64(36, true);
    const offDocLen = dv.getUint32(44, true);
    const offVocab = dv.getUint32(48, true);
    const offIdf = dv.getUint32(52, true);
    const offStart = dv.getUint32(56, true);
    const offPost = dv.getUint32(60, true);

    const docLen = new Uint16Array(buf, offDocLen, N);
    const idf = new Float32Array(buf, offIdf, V);
    const start = new Uint32Array(buf, offStart, V + 1);

    const vocab = new Array(V);
    const vi = new Map();
    let p = offVocab;
    for (let k = 0; k < V; k++) {
      const len = dv.getUint16(p, true);
      const t = dec.decode(new Uint8Array(buf, p + 2, len));
      vocab[k] = t;
      vi.set(t, k);
      p += 2 + len;
    }
    // 字节视图：varint 解码要按字节下标读，而 ArrayBuffer 不支持下标访问
    // （写成 buf[p] 会静默拿到 undefined，解码结果全是 0——这个坑踩过一次）。
    return { N, V, avgdl, k1, b, docLen, idf, start, vocab, vi, buf, u8: new Uint8Array(buf), offPost };
  }

  function createRetriever(IDX, META, CFG, DICT) {
    // DICT 是 jieba 高频词形（可选）：只用来决定「切在哪里」，
    // 打分仍然只认索引词表——不在词表里的词没有倒排表，自然无贡献。
    const POST_CACHE = new Map();

    // doc id -> 所属公司的块列表（分层召回按公司切分用）
    const byCode = new Map();
    META.codes.forEach(c => byCode.set(c, []));
    META.items.forEach((it, i) => byCode.get(META.codes[it[0]]).push(i));
    const keySec = new Set(META.keySec || []);

    function decodePostings(k) {
      const cached = POST_CACHE.get(k);
      if (cached) return cached;
      const buf = IDX.u8, start = IDX.start;
      let p = IDX.offPost + start[k];
      const end = IDX.offPost + start[k + 1];
      const out = [];
      let doc = -1;
      while (p < end) {
        let shift = 0, val = 0, byte;
        do { byte = buf[p++]; val |= (byte & 0x7f) << shift; shift += 7; } while (byte & 0x80);
        doc += val;                            // doc id 是 delta 编码
        shift = 0; val = 0;
        do { byte = buf[p++]; val |= (byte & 0x7f) << shift; shift += 7; } while (byte & 0x80);
        out.push(doc, val);
      }
      POST_CACHE.set(k, out);
      return out;
    }

    function tokenize(q) {
      // 没有 jieba：用索引词表做最大正向匹配。词表本来就是 jieba 切出来的，
      // 覆盖度足够；未登录的字退成单字，与 jieba 行为基本一致（15 题实测见评估脚本）。
      const out = [];
      const isWord = DICT
        ? s => IDX.vi.has(s) || DICT.has(s)
        : s => IDX.vi.has(s);
      let i = 0;
      const L = q.length;
      while (i < L) {
        let w = null;
        const maxL = Math.min(CFG.maxWordLen, L - i);
        for (let n = maxL; n >= 1; n--) {
          const s = q.slice(i, i + n);
          if (isWord(s)) { w = s; break; }
        }
        if (w) { out.push(w); i += w.length; continue; }
        const ch = q[i];
        if (/[0-9A-Za-z]/.test(ch)) {          // 数字/英文连成一个 token
          let j = i;
          while (j < L && /[0-9A-Za-z.\-%,]/.test(q[j])) j++;
          out.push(q.slice(i, j)); i = j; continue;
        }
        i++;                                    // 标点、空白丢弃
      }
      return out.filter(t => t.trim());
    }

    function scores(qtokens) {
      const { avgdl, k1, b, docLen, idf, vi } = IDX;
      const s = new Map();
      for (const t of qtokens) {
        const k = vi.get(t);
        if (k === undefined) continue;
        const w = idf[k];
        const pl = decodePostings(k);
        for (let i = 0; i < pl.length; i += 2) {
          const d = pl[i], tf = pl[i + 1];
          const add = w * (tf * (k1 + 1)) / (tf + k1 * (1 - b + b * docLen[d] / avgdl));
          s.set(d, (s.get(d) || 0) + add);
        }
      }
      return s;
    }

    function order(qtokens, cand) {
      // 与 Python 版 order() 同构：命中的按分降序，其余按 doc id 升序接后面。
      // 0 分的也要留着——它们可能被 +0.012 的公司先验顶上来。
      const sc = scores(qtokens);
      if (!cand) {
        const hit = [];
        for (const [i, v] of sc) hit.push([i, v]);
        hit.sort((x, y) => (y[1] - x[1]) || (x[0] - y[0]));
        const seen = new Set(hit.map(x => x[0]));
        const rest = [];
        for (let i = 0; i < IDX.N; i++) if (!seen.has(i)) rest.push(i);
        return hit.map(x => x[0]).concat(rest);
      }
      const inSet = new Set(cand);
      const hit = [];
      for (const [i, v] of sc) if (inSet.has(i)) hit.push([i, v]);
      hit.sort((x, y) => (y[1] - x[1]) || (x[0] - y[0]));
      const seen = new Set(hit.map(x => x[0]));
      const rest = cand.filter(i => !seen.has(i));
      return hit.map(x => x[0]).concat(rest);
    }

    function metaOf(i) {
      const it = META.items[i];
      return {
        i, code: META.codes[it[0]], name: META.names[it[0]], ord: it[1], page: it[2],
        section: it[3] >= 0 ? META.secs[it[3]] : '',
        subsection: it[4] >= 0 ? META.secs[it[4]] : '',
        kind: META.kinds[it[5]] || 'text',
      };
    }

    function detectCompany(q) {
      for (const code in CFG.companyAlias) {
        for (const a of CFG.companyAlias[code]) if (q.includes(a)) return code;
      }
      const m = q.match(/\b(\d{6})\b/);
      return m ? m[1] : null;
    }

    function detectCompanies(q) {
      const out = [];
      for (const code in CFG.companyAlias) {
        if (CFG.companyAlias[code].some(a => q.includes(a))) out.push(code);
      }
      return out;
    }

    function detectSectionHint(q) {
      let out = [];
      for (const kw in CFG.sectionHint) if (q.includes(kw)) out = out.concat(CFG.sectionHint[kw]);
      return out;
    }

    function search(query, topk, code) {
      const cand = code ? (byCode.get(code) || []) : null;
      const ord = order(tokenize(query), cand);
      const qCode = code || detectCompany(query);
      const qSecs = detectSectionHint(query);
      const hits = [];
      for (let r = 0; r < ord.length; r++) {
        const i = ord[r];
        const m = metaOf(i);
        let bonus = 0;
        if (qCode && m.code === qCode) bonus += CFG.wCompany;
        if (qSecs.length && qSecs.some(s => (m.section + m.subsection).includes(s))) {
          bonus += CFG.wSection;
        }
        const rrf = 1 / (CFG.rrfK + r + 1);
        hits.push({ i, rrf, score: rrf + bonus, bm25_rank: r + 1, m });
      }
      hits.sort((a, b) => (b.score - a.score) || (a.i - b.i));
      return hits.slice(0, topk);
    }

    function stratified(query, perCompany = 2, cap = 45, keySection = true) {
      let pooled = [];
      for (const code of META.codes) {
        pooled = pooled.concat(search(query, perCompany, code));
        if (keySection) {
          // 关键章节保底：Python 版查的是正文是否含"主要会计数据"等，
          // 这里查构建时预计算好的下标集合，判定口径一致但不依赖正文加载。
          const deep = search(query, 60, code);
          const sec = deep.find(h => keySec.has(h.i));
          if (sec) pooled.push(sec);
        }
      }
      pooled.sort((a, b) => b.score - a.score);
      const seen = new Set();
      const out = [];
      for (const h of pooled) {
        const key = h.m.code + ':' + h.m.ord;
        if (seen.has(key)) continue;
        seen.add(key);
        out.push(h);
      }
      return out.slice(0, cap);
    }

    function auto(query, topk) {
      // 点名一家 → 全局（公司先验够了，分层反而把别家硬塞进来把答案挤掉）
      // 没点名 或 点名多家 → 分层（否则名额被第一家垄断，覆盖不到 15 家）
      return detectCompanies(query).length === 1 ? search(query, topk) : stratified(query);
    }

    return { tokenize, search, stratified, auto, metaOf, detectCompanies, byCode, IDX, META, CFG };
  }

  return { parseIndex, createRetriever };
});
