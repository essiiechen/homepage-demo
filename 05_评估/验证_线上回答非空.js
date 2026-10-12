/* 验证线上站点（Vercel 静态页）的「答案」区在真实检索下不会为空。
 *
 * 做法：不启浏览器，直接在 Node 里加载线上同一份 assets（index.bin.gz / meta.json /
 * config.json / dict.txt.gz / text/<code>.json）与同一个 retriever.js，
 * 跑与 app.js 里 answerExtractive 完全相同的逻辑，看答案串是否非空。
 *
 * 用法： node 05_评估/验证_线上回答非空.js [站点根URL]
 */
const zlib = require('zlib');
const BASE = process.argv[2] || 'https://homepage-demo-umber.vercel.app';
const MOD = require(BASE ? '/tmp/retriever.js' : '/tmp/retriever.js'); // 线上同一份检索脚本

async function get(path) {
  const r = await fetch(BASE + path);
  if (!r.ok) throw new Error(`${path} -> HTTP ${r.status}`);
  return Buffer.from(await r.arrayBuffer());
}
const gunzipMaybe = b => (b[0] === 0x1f && b[1] === 0x8b) ? zlib.gunzipSync(b) : b;

const QUESTIONS = [
  '贵州茅台2025年营业收入是多少？同比变化多少？',
  '五粮液2025年归属于上市公司股东的净利润是多少？同比下降多少？',
  '五粮液酒类产品的毛利率是多少？',
  '贵州茅台2025年度的利润分配方案是什么？每10股派多少？',
  '洋河股份2025年末经销商数量是多少？比上年变化多少？',
  '山西汾酒2025年营业收入是多少？同比增速多少？',
  '这15家白酒公司里，2025年营业收入同比增长最快的是哪一家？增速是多少？',
  '这15家公司里，哪家2025年归属于上市公司股东的净利润是亏损的？亏了多少？',
  '这15家公司里，哪家公司的酒类（或主营业务）毛利率最高？',
  '贵州茅台和五粮液，谁的酒类（主营业务）毛利率更高？各是多少？',
  '今世缘、口子窖、老白干酒这三家公司，2025年营业收入最高的是哪家？是多少？',
  '贵州茅台2025年的营业收入、归母净利润、酒类毛利率分别是多少？',
  '五粮液2025年营业收入相比2024年下降了多少？降幅是多少？',
  '贵州茅台2025年度财务报表被出具了什么类型的审计意见？',
  '贵州茅台2025年末在职员工总人数是多少？',
];

(async () => {
  const idxBuf = gunzipMaybe(await get('/assets/index.bin.gz'));
  const META = JSON.parse((await get('/assets/meta.json')).toString('utf8'));
  const CFG = JSON.parse((await get('/assets/config.json')).toString('utf8'));
  const DICT = new Set(gunzipMaybe(await get('/assets/dict.txt.gz')).toString('utf8').split('\n'));

  const IDX = MOD.parseIndex(idxBuf.buffer.slice(idxBuf.byteOffset, idxBuf.byteOffset + idxBuf.byteLength));
  const R = MOD.createRetriever(IDX, META, CFG, DICT);
  console.log(`索引就绪：${IDX.N} 块 / ${IDX.V} 词 / ${META.codes.length} 家`);

  const TXT = new Map();
  async function ensureText(codes) {
    for (const c of codes) {
      if (TXT.has(c)) continue;
      TXT.set(c, JSON.parse((await get(`/assets/text/${c}.json`)).toString('utf8')).t);
    }
  }
  const textOf = m => (TXT.get(m.code) || [])[m.ord] || '';

  // 与 app.js 中 answerExtractive 逐字一致
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

  let empty = 0;
  for (let i = 0; i < QUESTIONS.length; i++) {
    const q = QUESTIONS[i];
    const hits = R.auto(q, 8);
    await ensureText([...new Set(hits.map(h => h.m.code))]);
    const ans = answerExtractive(q, hits);
    const cov = new Set(hits.map(h => h.m.code)).size;
    const ok = ans && ans.trim().length > 0;
    if (!ok) empty++;
    console.log(`Q${i + 1}  答案${ok ? '非空' : '★空★'}  ${ans.length}字  覆盖${cov}/15家  ${q.slice(0, 24)}`);
    if (!ok) console.log('   ↑ 这一题答案为空');
  }
  console.log(`\n结论：15 题中答案区为空的有 ${empty} 题`);
})().catch(e => { console.error('失败：', e); process.exit(1); });
