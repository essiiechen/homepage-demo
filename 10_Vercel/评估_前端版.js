#!/usr/bin/env node
/* 前端版（assets/retriever.js）在 15 题上的实测得分。
 *
 * 关键点：这里 require 的就是浏览器里跑的那一份代码，不是照着重写一遍。
 * 评估对象错了，分数再高也没意义——Python 版与前端版必须用同一批题、
 * 同一套 gold 判据来对齐。
 *
 * 用法： node 10_Vercel/评估_前端版.js
 */
'use strict';
const fs = require('fs');
const zlib = require('zlib');
const path = require('path');

const ROOT = path.resolve(__dirname, '..');
const BJ = require(path.join(ROOT, 'assets', 'retriever.js'));

const idx = BJ.parseIndex(zlib.gunzipSync(
  fs.readFileSync(path.join(ROOT, 'assets', 'index.bin.gz'))));
const meta = JSON.parse(fs.readFileSync(path.join(ROOT, 'assets', 'meta.json'), 'utf8'));
const cfg = JSON.parse(fs.readFileSync(path.join(ROOT, 'assets', 'config.json'), 'utf8'));
const qs = JSON.parse(fs.readFileSync(path.join(__dirname, '题库_15.json'), 'utf8'));
// 与浏览器同一份 jieba 高频词形，分词口径必须一致
const dictPath = path.join(ROOT, 'assets', 'dict.txt.gz');
const dict = fs.existsSync(dictPath)
  ? new Set(zlib.gunzipSync(fs.readFileSync(dictPath)).toString('utf8').split('\n'))
  : null;
const R = BJ.createRetriever(idx, meta, cfg, dict);

const TEXT = new Map();
function textOf(m) {
  if (!TEXT.has(m.code)) {
    TEXT.set(m.code, JSON.parse(fs.readFileSync(
      path.join(ROOT, 'assets', 'text', m.code + '.json'), 'utf8')).t);
  }
  return (TEXT.get(m.code) || [])[m.ord] || '';
}

// ---- 判分：照抄 05_评估/评估_15题.py 的 gold_hit ----
function subHit(hits, sub) {
  for (const h of hits) {
    if (h.m.code !== sub.code) continue;
    for (const kw of sub.any) if (textOf(h.m).includes(kw)) return true;
  }
  return false;
}

function goldHit(hits, gold) {
  if ('min_companies' in gold) {
    const n = new Set(hits.map(h => h.m.code)).size;
    return [n >= gold.min_companies, n, `覆盖 ${n} 家（要求 ≥${gold.min_companies}）`];
  }
  if ('all' in gold) {
    const got = gold.all.map(s => subHit(hits, s));
    const n = got.filter(Boolean).length;
    return [n === gold.all.length, `${n}/${gold.all.length}`,
      n === gold.all.length ? '全部要素命中' : '缺要素'];
  }
  const found = [];
  hits.forEach((h, idx_) => {
    if (h.m.code !== gold.code) return;
    for (const kw of gold.any) {
      if (textOf(h.m).includes(kw)) { found.push([kw, idx_ + 1]); break; }
    }
  });
  const n = new Set(found.map(f => f[0])).size;
  const need = gold.min_hits || 1;
  return [n >= need, n, `命中 ${n} 个关键数字（要求 ≥${need}）`];
}

const STRATEGIES = {
  global: q => R.search(q, 8),
  stratified: q => R.stratified(q),
  auto: q => R.auto(q, 8),
};

const score = {};
for (const k in STRATEGIES) score[k] = 0;
console.log(`前端版数据：${idx.N} 块 / ${idx.V} 词\n`);

// 落盘一份，供 06_结论/生成_一页结论.py 引用（PDF 里的数字必须有文件可查）
const detail = [];
for (const qd of qs) {
  let line = `Q${String(qd.id).padStart(2)}`;
  const row = { '题号': qd.id, '问题': qd.q };
  for (const name in STRATEGIES) {
    const hits = STRATEGIES[name](qd.q).slice(0, 8);   // 判定口径统一取前 8 块
    const [ok, pos, why] = goldHit(hits, qd.gold);
    if (ok) score[name]++;
    row[name] = ok ? '✓' : '✗';
    line += `  ${name.slice(0, 4)}=${ok ? '✓' : '✗'}`;
  }
  detail.push(row);
  console.log(line);
}

require('fs').writeFileSync(
  require('path').join(__dirname, '结果_前端版.json'),
  JSON.stringify({ '块数': idx.N, '词数': idx.V, '题数': qs.length,
                   '得分': score, '逐题': detail }, null, 1),
  'utf8');

console.log();
for (const k in score) console.log(`  ${k.padEnd(12)} ${score[k]}/${qs.length}`);
const best = Object.keys(score).reduce((a, b) => score[a] >= score[b] ? a : b);
console.log(`\n最优策略：${best}（${score[best]}/${qs.length}）—— 线上默认用 auto`);
console.log('对照：Python 版纯 BM25 自适应 9/15（10_Vercel/评估_线上策略.py）');
