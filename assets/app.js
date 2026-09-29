/* 页面交互层：只管加载资源、渲染、拿正文，检索逻辑全在 assets/retriever.js。
 *
 * 正文为什么要按公司分片按需拉取：
 *   全部正文 8.97MB，一次下完手机流量要等十几秒。但打分只需要倒排表（1.15MB），
 *   正文只在展示 top-k 时才用得上。所以只拉"要展示的这几块所属公司"的分片。
 */
(() => {
  'use strict';
  const $ = s => document.querySelector(s);
  let R = null, IDX = null, META = null, STATS = null;
  const TEXT_CACHE = new Map();
  let busy = false;

  async function gunzip(buf) {
    if (typeof DecompressionStream !== 'function') {
      throw new Error('当前浏览器不支持 DecompressionStream，请换新版 Chrome / Safari / Edge');
    }
    const ds = new DecompressionStream('gzip');
    return await new Response(new Blob([buf]).stream().pipeThrough(ds)).arrayBuffer();
  }

  async function fetchIndex() {
    const resp = await fetch('assets/index.bin.gz');
    if (!resp.ok) throw new Error('索引下载失败：HTTP ' + resp.status);
    let buf = await resp.arrayBuffer();
    // 关键容错：CDN 可能自己带 Content-Encoding: gzip，浏览器已经解压过了。
    // 所以只看内容里的 gzip 魔数，不看扩展名——否则会二次解压失败。
    const head = new Uint8Array(buf, 0, 2);
    if (head[0] === 0x1f && head[1] === 0x8b) buf = await gunzip(buf);
    return buf;
  }

  async function loadDict() {
    // jieba 高频词形：只影响「切在哪里」，不参与打分
    const resp = await fetch('assets/dict.txt.gz');
    if (!resp.ok) return null;
    let buf = await resp.arrayBuffer();
    const head = new Uint8Array(buf, 0, 2);
    if (head[0] === 0x1f && head[1] === 0x8b) buf = await gunzip(buf);
    return new Set(new TextDecoder('utf-8').decode(buf).split('\n'));
  }

  async function boot() {
    const t0 = performance.now();
    const [idxBuf, meta, cfg, stats, dict] = await Promise.all([
      fetchIndex(),
      fetch('assets/meta.json').then(r => r.json()),
      fetch('assets/config.json').then(r => r.json()),
      fetch('assets/stats.json').then(r => r.json()).catch(() => null),
      loadDict(),
    ]);
    IDX = BJ.parseIndex(idxBuf);
    META = meta;
    R = BJ.createRetriever(IDX, META, cfg, dict);
    STATS = stats;

    const ms = Math.round(performance.now() - t0);
    $('#boot').textContent = `索引就绪 · ${IDX.N} 块 / ${IDX.V} 词 · 加载 ${ms}ms`;
    $('#go').disabled = false;
    $('#q').disabled = false;
    if (STATS) {
      const set = (s, v) => { const e = $(s); if (e) e.textContent = v; };
      set('#c-company', STATS.n_companies);
      set('#c-page', STATS.n_pages);
      set('#c-table', STATS.n_tables);
      set('#c-chunk', STATS.n_chunks);
    }
  }

  async function ensureText(codes) {
    const need = codes.filter(c => !TEXT_CACHE.has(c));
    await Promise.all(need.map(async c => {
      const r = await fetch(`assets/text/${c}.json`);
      if (!r.ok) throw new Error('正文分片加载失败：' + c);
      TEXT_CACHE.set(c, (await r.json()).t);
    }));
  }

  const textOf = m => (TEXT_CACHE.get(m.code) || [])[m.ord] || '';

  // ---------------------------------------------------------------- 答案
  function answerExtractive(query, hits, maxBlocks = 4) {
    const qTerms = new Set(R.tokenize(query).filter(t => t.length >= 2));
    const lines = [];
    let used = 0;
    for (const h of hits.slice(0, maxBlocks)) {
      const m = h.m;
      const txt = textOf(m);
      let sents = txt.split(/(?<=[。；;!?！？])\s*/).map(s => s.trim()).filter(Boolean);
      if (!sents.length) sents = [txt.slice(0, 200)];
      const scored = sents.map(s => {
        let n = 0;
        for (const t of qTerms) if (s.includes(t)) n++;
        return [n + (/\d/.test(s) ? 0.5 : 0), -s.length, s];
      });
      scored.sort((a, b) => (b[0] - a[0]) || (b[1] - a[1]));
      const body = scored.slice(0, 2).map(x => x[2]).join(' ');
      if (used + body.length > 900 && lines.length) break;
      used += body.length;
      const sec = m.section + (m.subsection ? ' / ' + m.subsection : '');
      lines.push(`[${m.name}（${m.code}）第${m.page}页${sec ? ' · ' + sec : ''}]\n${body}`);
    }
    return lines.join('\n\n');
  }

  // ---------------------------------------------------------------- 渲染
  function esc(s) {
    return (s || '').replace(/[&<>]/g, m => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[m]));
  }

  function hl(text, qtokens) {
    const terms = [...new Set(qtokens.filter(t => t.length >= 2))].slice(0, 12);
    let out = esc(text);
    for (const t of terms) out = out.split(esc(t)).join(`<mark class="hl">${esc(t)}</mark>`);
    return out;
  }

  function fmtAnswer(t) {
    return esc(t).replace(/\[([^\]]{0,60}?)第(\d+)页([^\]]*?)\]/g,
      (mm, a, b) => `<citeflag>${a.trim()} 第${b}页</citeflag>`);
  }

  async function ask() {
    const q = $('#q').value.trim();
    if (!q || busy || !R) return;
    busy = true; $('#go').disabled = true;
    $('#ansState').innerHTML = '<span class="spin"></span>检索中';
    $('#answer').innerHTML = '<span class="empty">正在检索…</span>';
    $('#meta').innerHTML = '';
    try {
      const t0 = performance.now();
      const strategy = $('#strategy').value;
      const topk = Math.max(1, Math.min(+$('#topk').value || 8, 40));
      const code = $('#code').value || null;
      let hits;
      if (code) hits = R.search(q, topk, code);
      else if (strategy === 'global') hits = R.search(q, topk);
      else if (strategy === 'stratified') hits = R.stratified(q);
      else hits = R.auto(q, topk);
      const tRet = performance.now() - t0;

      const codes = [...new Set(hits.map(h => h.m.code))];
      const t1 = performance.now();
      await ensureText(codes);
      const tTxt = performance.now() - t1;

      hits.forEach((h, k) => { h.rank = k + 1; });
      const qtokens = R.tokenize(q);
      $('#hitN').textContent = hits.length;
      $('#hits').innerHTML = hits.map(h => {
        const m = h.m, txt = textOf(m);
        return `<div class="hit${h.rank <= 2 ? ' open' : ''}">
          <div class="hd" onclick="this.parentNode.classList.toggle('open')">
            <span class="rk">${h.rank}</span>
            <span class="co">${esc(m.name)}</span>
            <span class="pg">第${m.page}页</span>
            <span class="sec">${esc(m.section)}${m.subsection ? ' / ' + esc(m.subsection) : ''}</span>
            <span class="tag">${m.kind === 'table' ? '表格' : '正文'}
              <span class="s">bm25 #${h.bm25_rank}</span>
              <span class="s">${txt.length}字</span></span>
          </div>
          <div class="bd">${hl(txt, qtokens)}</div>
        </div>`;
      }).join('') || '<span class="empty">没有召回任何块。</span>';

      const ans = answerExtractive(q, hits);
      $('#answer').innerHTML = ans ? fmtAnswer(ans)
        : '<span class="empty">没有摘到原文，请展开右侧召回块查看。</span>';
      $('#ansState').textContent = '已生成';
      $('#meta').innerHTML = `检索 ${Math.round(tRet)}ms ｜ 正文加载 ${Math.round(tTxt)}ms`
        + ` ｜ 前端 BM25 ｜ 候选 ${IDX.N} 块 ｜ 本轮覆盖 ${new Set(hits.map(h => h.m.code)).size}/${META.codes.length} 家`
        + ` ｜ 命中词 ${qtokens.length} 个`;
    } catch (e) {
      $('#answer').innerHTML = '<span class="empty">出错：' + esc(String(e)) + '</span>';
      $('#ansState').textContent = '出错';
    } finally {
      busy = false; $('#go').disabled = false;
    }
  }

  window.addEventListener('DOMContentLoaded', () => {
    $('#go').disabled = true; $('#q').disabled = true;
    $('#boot').textContent = '正在加载索引…';
    boot().catch(e => {
      $('#boot').textContent = '索引加载失败';
      $('#answer').innerHTML = '<span class="empty">' + esc(String(e)) + '</span>';
    });
    document.querySelector('.tips').addEventListener('click', e => {
      if (e.target.classList.contains('ex')) { $('#q').value = e.target.textContent.trim(); ask(); }
    });
    $('#q').addEventListener('keydown', e => { if (e.key === 'Enter') ask(); });
    $('#go').addEventListener('click', ask);
  });
})();
