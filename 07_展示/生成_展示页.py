# -*- coding: utf-8 -*-
"""生成线上展示页 index.html（静态、单文件、零依赖）

- 截图压缩后以 base64 内嵌，PDF 以 data URI 内嵌 => 仓库不需要二进制文件，
  Vercel / GitHub Pages / 任意静态托管都能直接跑。
- 逐题记录从 05_评估/逐题记录.csv 读取，保证页面数字与评估结果同源。
"""
import base64
import csv
import io
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "index.html"

SHOTS = [
    ("01", "单公司事实题：答案带『公司 + 页码』出处", "06_结论/截图_01_单公司题.png"),
    ("02", "跨公司题（全局 top-8）：只覆盖 5/15 家公司", "06_结论/截图_02_跨公司题_全局top8.png"),
    ("03", "跨公司题（分层召回）：覆盖 15/15 家公司", "06_结论/截图_03_跨公司题_分层召回.png"),
]

from PIL import Image  # noqa: E402


def b64_img(path, width=1200, quality=78):
    im = Image.open(path).convert("RGB")
    w, h = im.size
    if w > width:
        im = im.resize((width, int(h * width / w)), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=quality, optimize=True)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def load_rows():
    p = ROOT / "05_评估" / "逐题记录.csv"
    with open(p, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def esc(s):
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


CSS = """
*{box-sizing:border-box}
body{margin:0;background:#f6f7f9;color:#1f2328;
 font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei","Helvetica Neue",Arial,sans-serif;
 line-height:1.7;-webkit-font-smoothing:antialiased}
.wrap{max-width:1080px;margin:0 auto;padding:0 20px 64px}
header{background:linear-gradient(135deg,#0f766e,#115e59);color:#fff;padding:44px 20px 38px;margin-bottom:28px}
header .inner{max-width:1080px;margin:0 auto}
h1{margin:0 0 10px;font-size:28px;letter-spacing:.3px}
.sub{opacity:.92;font-size:15px;margin:0}
.badges{margin-top:16px;display:flex;flex-wrap:wrap;gap:8px}
.badge{background:rgba(255,255,255,.16);border:1px solid rgba(255,255,255,.28);
 border-radius:999px;padding:4px 12px;font-size:13px}
h2{font-size:20px;margin:34px 0 14px;padding-left:11px;border-left:4px solid #0f766e}
h3{font-size:16px;margin:22px 0 10px;color:#0f766e}
p{margin:10px 0}
.card{background:#fff;border:1px solid #e4e7eb;border-radius:12px;padding:20px 22px;
 box-shadow:0 1px 3px rgba(16,24,40,.05)}
.lead{background:#fff8e6;border:1px solid #f0d69a;border-radius:12px;padding:18px 20px;font-size:15px}
.lead b{color:#8a5a00}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin-top:6px}
.stat{background:#fff;border:1px solid #e4e7eb;border-radius:12px;padding:14px 16px;text-align:center}
.stat .n{font-size:24px;font-weight:700;color:#0f766e}
.stat .t{font-size:13px;color:#667085;margin-top:2px}
table{width:100%;border-collapse:collapse;background:#fff;font-size:14px;
 border:1px solid #e4e7eb;border-radius:10px;overflow:hidden}
th{background:#f2f4f7;text-align:left;padding:10px 12px;font-weight:600;white-space:nowrap}
td{padding:10px 12px;border-top:1px solid #eef0f3;vertical-align:top}
tr:nth-child(even) td{background:#fcfcfd}
.ok{color:#0f766e;font-weight:600}
.no{color:#b3261e;font-weight:600}
.warn{color:#b45309;font-weight:600}
.tag{display:inline-block;font-size:12px;padding:1px 8px;border-radius:6px;background:#eef2f6;color:#475467;margin-right:6px}
.shots{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:16px}
figure{margin:0;background:#fff;border:1px solid #e4e7eb;border-radius:12px;overflow:hidden}
figure img{display:block;width:100%;height:auto}
figcaption{padding:10px 14px;font-size:13px;color:#475467;border-top:1px solid #eef0f3}
code,pre{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:13px}
pre{background:#1f2328;color:#e6edf3;padding:14px 16px;border-radius:10px;overflow:auto}
.note{font-size:13px;color:#667085;margin-top:8px}
.pdfbox{background:#fff;border:1px solid #e4e7eb;border-radius:12px;padding:12px}
.pdfbox iframe{width:100%;height:620px;border:0;border-radius:8px;display:block}
a{color:#0f766e}
footer{margin-top:40px;padding-top:18px;border-top:1px solid #e4e7eb;font-size:13px;color:#667085}
@media(max-width:640px){h1{font-size:22px}table{font-size:13px}}
"""


def main():
    rows = load_rows()
    shots = {k: b64_img(ROOT / p) for k, _, p in SHOTS}
    pdf = "data:application/pdf;base64," + base64.b64encode(
        (ROOT / "06_结论" / "一页结论.pdf").read_bytes()
    ).decode()

    # 题型汇总
    single = [r for r in rows if r["类型"].startswith("单公司")]
    cross = [r for r in rows if r["类型"].startswith("跨公司")]
    s_ok = sum(1 for r in single if r["gold块召回"] == "是")
    c_ok = sum(1 for r in cross if r["gold块召回"] == "是")

    trs = []
    for r in rows:
        ok = r["gold块召回"] == "是"
        judge = r["答对没有"]
        cls = "ok" if judge == "能答对" else ("warn" if judge.startswith("碰巧") else "no")
        top = " ｜ ".join(r["召回块（hybrid top-8）"].split(" ｜ ")[:4]) + " …"
        trs.append(f"""<tr>
<td><span class="tag">Q{r['题号']}</span>{esc(r['类型'])}</td>
<td>{esc(r['问题'])}</td>
<td>{esc(r['标准答案（回原文核过）'])}</td>
<td style="font-size:12.5px;color:#475467">{esc(top)}<br><span class="tag">覆盖 {esc(r['覆盖公司数'])}</span></td>
<td class="{cls}">{esc(judge)}{'' if ok else '<br><span style="font-weight:400;font-size:12.5px;color:#667085">' + esc(r['错在哪']) + '</span>'}</td>
</tr>""")

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>白酒年报问答知识库 · 作业展示</title>
<style>{CSS}</style>
</head>
<body>
<header><div class="inner">
<h1>白酒上市公司年报问答知识库</h1>
<p class="sub">《学会使用大模型》第三周作业 · 方向 A ｜ 15 家白酒公司 2025 年报 · BM25 + bge-small-zh 向量混合检索</p>
<div class="badges">
<span class="badge">方向：知识库（检索）</span>
<span class="badge">2,698 页 / 3,988 张表格 / 9,762 个文本块</span>
<span class="badge">10 题逐题记录 · 判定基于检索，不依赖大模型</span>
</div>
</div></header>

<div class="wrap">

<h2>一句话结论</h2>
<div class="lead">
<b>单公司事实题，检索 7/7 全部命中；跨公司全景题，top-8 检索下 gold 块 0/3。</b><br>
根因不是模型不行、也不是数据缺失，而是 <b>top-k 语义截断</b>：一次只取 8 块，最多覆盖 8 家公司，
“15 家里谁最……”这类题<b>结构上不可能</b>召回全。只换召回策略（分层召回：每家公司各取 2 块）、
不改任何模型参数，覆盖率 5/15 → 15/15，3/3 全部召回。
</div>

<h2>数据规模</h2>
<div class="grid">
<div class="stat"><div class="n">15</div><div class="t">家公司年报</div></div>
<div class="stat"><div class="n">2,698</div><div class="t">解析页数</div></div>
<div class="stat"><div class="n">3,988</div><div class="t">还原表格（按行列）</div></div>
<div class="stat"><div class="n">9,762</div><div class="t">切块（带公司/章节/页码）</div></div>
<div class="stat"><div class="n">241万</div><div class="t">字</div></div>
</div>

<h2>一、10 道题的检索结果</h2>
<div class="card">
<p style="margin-top:0">判据：<b>含标准答案的原文块（gold 块）是否被召回进 top-8</b>。标准答案全部回原文核对过，
不是凭印象写的（我第一版金标准自己错了一题，见文末注脚）。</p>
<table>
<thead><tr><th>题目</th><th>问题</th><th>标准答案</th><th>hybrid 召回块（前 4）</th><th>判定 / 错在哪</th></tr></thead>
<tbody>
{chr(10).join(trs)}
</tbody>
</table>
<p class="note">汇总：单公司事实题 <b class="ok">{s_ok}/{len(single)} gold 块召回成功</b>；
跨公司全景题 <b class="no">{c_ok}/{len(cross)}</b>（Q9 判定“碰巧能答对”——茅台恰好挤进召回子集，但只比较 5 家就下“最高”结论，换问法即翻车）。</p>
</div>

<h2>二、为什么翻车：换召回策略，不动模型</h2>
<div class="card">
<table>
<thead><tr><th>召回策略</th><th>覆盖公司数</th><th>Q7 谁增速最快</th><th>Q8 谁亏损</th><th>Q9 毛利率最高</th></tr></thead>
<tbody>
<tr><td>A 全局 top-8（默认）</td><td>5 / 15</td><td class="no">失败</td><td class="no">失败</td><td class="warn">碰巧对</td></tr>
<tr><td>B 全局 top-40</td><td>12–14 / 15</td><td class="no">失败</td><td class="warn">部分</td><td class="ok">成功</td></tr>
<tr><td>C 分层召回（15 家 × 2 块）</td><td>15 / 15</td><td class="ok">成功</td><td class="ok">成功</td><td class="ok">成功</td></tr>
</tbody>
</table>
<p class="note">同一份数据、同一个向量模型、同一套权重，<b>只改召回方式</b>，跨公司题就从 0/3 修到 3/3。
这说明瓶颈在“取哪些块进上下文”，不在“用什么模型算相似度”。</p>
</div>

<h2>三、页面截图（真实运行结果）</h2>
<div class="shots">
{"".join(f'<figure><img loading="lazy" src="{shots[k]}" alt="{esc(t)}"><figcaption><b>{k}</b> · {esc(t)}</figcaption></figure>' for k, t, _ in SHOTS)}
</div>
<p class="note">截图 02 与 03 是同一个问题的两种召回策略：02 右侧只出现 5 家公司，03 覆盖全部 15 家。</p>

<h2>四、一页结论 PDF</h2>
<div class="pdfbox">
<iframe src="{pdf}" title="一页结论 PDF"></iframe>
</div>
<p class="note">若浏览器不显示内嵌 PDF，可直接下载仓库里的 <code>06_结论/一页结论.pdf</code>，
或本地运行 <code>python 06_结论/生成_一页结论.py</code> 重新生成。</p>

<h2>五、复现方式</h2>
<div class="card">
<pre>pip install -r requirements.txt

python 01_下载/下载_财报.py          # 巨潮 API 下载 15 份年报 PDF（可重复跑）
python 02_解析/解析_财报.py          # pdfplumber：文字 + 表格按行列还原 + 页码
python 03_索引/切块.py               # 按章节切块，带公司/章节/页码
python 03_索引/构建_索引.py --backend sbert --tag bge
python 04_问答/app.py                # 问答页面 http://127.0.0.1:8791
python 05_评估/评估_10题.py           # 10 题 × 3 种检索模式逐题记录
python 05_评估/对比_跨公司召回策略.py  # 全局 top-8 / top-40 / 分层召回对照
python 06_结论/生成_一页结论.py       # 一页结论 PDF</pre>
<p class="note">年报 PDF（约 30MB）不进仓库，用上面的脚本一键重建；API key 只放 <code>.env</code>，<code>.gitignore</code> 已排除。
问答页面在无 key 时自动降级为纯检索模式。</p>
</div>

<h2>六、结论：同一批财报，什么该建库、什么该微调</h2>
<div class="card">
<h3 style="margin-top:0">① 建库（检索）</h3>
<p>点名了公司、要某个具体数字或事实的问题。答案是原文里的一行，检索能精确命中、还带得出处
（本次 7/7，最好第 1 位命中）；年报年年换，库可以增量重建。</p>
<h3>② 微调</h3>
<p>要稳定口径、术语、组织答案方式的问题（例如“用董秘的语气解释为什么下滑”）。检索只能抄原文，
学不会“先行业、再公司、后措施”的叙述结构——这类才是微调的位置。</p>
<h3>③ 两个都不对</h3>
<p>跨全部实体聚合、排序、比较。top-k 语义截断做不了全集扫描，微调更不该用来记数字。
正解是把关键指标 ETL 成结构化宽表再读表。</p>
</div>

<h2>注脚（诚实交代两件事）</h2>
<div class="card">
<p>① 第一版标准答案我自己就错了一题：Q8 只写了酒鬼酒 −3,395 万，漏了亏得更多的顺鑫农业 −1.52 亿，
是回原文核对才发现的；Q9 原本写成“口径不统一无法排序”，核对后修正为贵州茅台 91.23%。
这反过来印证：人对数字的记忆不可靠，检索到的原文才可靠。</p>
<p>② 本次 10 题的判定<b>全部基于检索</b>（gold 块是否被召回进 top-8、召回了哪些块、错在哪），
不依赖任何大模型；页面里的 LLM 答案生成只是可选增强，无 key 时自动降级。</p>
</div>

<footer>
《学会使用大模型》第三周作业 · 方向 A ｜ 材料：15 家白酒上市公司 2025 年年度报告（巨潮资讯网公开 PDF）
</footer>
</div>
</body>
</html>
"""
    OUT.write_text(html, encoding="utf-8")
    print(f"已生成 {OUT}  {OUT.stat().st_size/1024:.0f} KB")
    print(f"单公司 {s_ok}/{len(single)}，跨公司 {c_ok}/{len(cross)}")


if __name__ == "__main__":
    main()
