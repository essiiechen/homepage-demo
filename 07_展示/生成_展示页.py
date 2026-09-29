# -*- coding: utf-8 -*-
"""生成线上展示页 index.html（静态、单文件、零依赖）

- 截图压缩后以 base64 内嵌，PDF 以 data URI 内嵌 => 仓库不需要二进制文件，
  Vercel / GitHub Pages / 任意静态托管都能直接跑。
- 逐题记录从 05_评估/逐题记录.csv、策略矩阵从 对比_召回策略.json 读取，
  保证页面数字与评估结果同源。
"""
import base64
import csv
import io
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# 输出必须在 07_展示/ 下：仓库根的 index.html 是静态问答应用的入口，不能被覆盖
# （这个坑真实发生过——脚本一度写到根上，把问答页盖掉了，靠 git checkout 才救回来）
OUT = ROOT / "07_展示" / "index.html"

# 真正能提问的问答应用（Flask + 混合检索）已部署在这里；
# 本页只是评估报告，所以要把它放在最显眼的位置，别让看的人以为这就是系统本身。
LIVE_URL = "https://baijiu-report-rag.app-tencent.workbuddy.host/"

SHOTS = [
    ("01", "单公司事实题：答案带『公司 + 页码』出处", "06_结论/截图_01_单公司题.png"),
    ("02", "跨公司题（全局 top-8）：只覆盖 6/15 家公司", "06_结论/截图_02_跨公司题_全局top8.png"),
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


def esc(s):
    return (str(s) or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


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
.livebar{background:#eef6ff;border-bottom:1px solid #cfe0fb}
.livebar .inner{max-width:1080px;margin:0 auto;padding:14px 20px;font-size:15px}
.livebar a{color:#1156c4;font-weight:600;text-decoration:none;border-bottom:1px solid #9dbdf5}
.livebar a:hover{color:#0b3f96}
.livebar .note{display:block;margin-top:4px;color:#5a6b85;font-size:13px}
h2{font-size:20px;margin:34px 0 14px;padding-left:11px;border-left:4px solid #0f766e}
h3{font-size:16px;margin:22px 0 10px;color:#0f766e}
p{margin:10px 0}
.card{background:#fff;border:1px solid #e4e7eb;border-radius:12px;padding:20px 22px;
 box-shadow:0 1px 3px rgba(16,24,40,.05);overflow-x:auto}
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
.pdfbox iframe{width:100%;height:640px;border:0;border-radius:8px;display:block}
a{color:#0f766e}
footer{margin-top:40px;padding-top:18px;border-top:1px solid #e4e7eb;font-size:13px;color:#667085}
@media(max-width:640px){h1{font-size:22px}table{font-size:13px}}
"""


def main():
    # ---------- 数据 ----------
    with open(ROOT / "05_评估" / "逐题记录.csv", encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))
    strat = json.loads((ROOT / "05_评估" / "对比_召回策略.json").read_text(encoding="utf-8"))
    pdf = "data:application/pdf;base64," + base64.b64encode(
        (ROOT / "06_结论" / "一页结论.pdf").read_bytes()).decode()
    shots = {k: b64_img(ROOT / p) for k, _, p in SHOTS}

    g_total, g_ok = Counter(), Counter()
    for r in rows:
        g_total[r["分组"]] += 1
        if r["答对没有"].startswith(("能答对", "碰巧")):
            g_ok[r["分组"]] += 1
    n_ok = sum(1 for r in rows if r["答对没有"].startswith(("能答对", "碰巧")))

    order = ["A 全局top-8", "B 全局top-40", "C 分层(15家×2)", "D 分层(15家×4)",
             "E 分层+关键章节保底"]
    qids = [7, 8, 9, 10, 11, 12]
    cell, blocks, cover = {}, {}, {}
    for r in strat:
        cell[(r["策略"], r["题号"])] = r["判定"]
        blocks[r["策略"]] = r["送进模型的块数"]
        cover[r["策略"]] = r["覆盖公司数"]
    MARK = {"✓ 够答": ("够", "ok"), "△ 部分": ("半", "warn"), "✗ 不够": ("缺", "no")}

    # ---------- 逐题表 ----------
    trs = []
    for r in rows:
        judge = r["答对没有"]
        cls = "ok" if judge == "能答对" else ("warn" if judge.startswith("碰巧") else "no")
        top = " ｜ ".join(r["召回块（hybrid top-8）"].split(" ｜ ")[:4]) + " …"
        trs.append(f"""<tr>
<td><span class="tag">Q{r['题号']}</span>{esc(r['类型'])}</td>
<td>{esc(r['问题'])}</td>
<td>{esc(r['标准答案（回原文核过）'])}</td>
<td style="font-size:12.5px;color:#475467">{esc(top)}<br><span class="tag">覆盖 {esc(r['覆盖公司数'])}</span></td>
<td class="{cls}">{esc(judge)}<br><span style="font-weight:400;font-size:12.5px;color:#667085">{esc(r['错在哪'])}</span></td>
</tr>""")

    # ---------- 分组表 ----------
    TIP = {
        "① 单公司·一个数字": "答案就是原文里的一行：全部召回，最好第 1 位命中，出处精确到『公司 + 页码』",
        "② 跨公司·全景（无候选集）": "top-8 最多覆盖 8 家公司，『15 家里谁最…』结构上覆盖不全；唯一『对』的是碰巧",
        "③ 跨公司·受限（给了候选集）": "对照组：即使点名了公司也全错——全局排序会垄断名额（Q10 只覆盖 1/15 家）",
        "④ 单公司·要拼多块": "要 3 个数字，top-8 被同一章节占满，只召回 1 个",
    }
    gtr = ""
    for g in ["① 单公司·一个数字", "② 跨公司·全景（无候选集）",
              "③ 跨公司·受限（给了候选集）", "④ 单公司·要拼多块"]:
        ok, tot = g_ok[g], g_total[g]
        cls = "ok" if ok == tot else ("no" if ok == 0 else "warn")
        gtr += (f"<tr><td>{esc(g)}</td><td>{tot}</td>"
                f"<td class='{cls}'>{ok}/{tot}</td><td>{esc(TIP[g])}</td></tr>")

    # ---------- 策略矩阵 ----------
    mtr = ""
    for s in order:
        mtr += (f"<tr><td>{esc(s)}</td><td>{blocks[s]}</td><td>{cover[s]}/15</td>"
                + "".join(
                    f"<td class='{MARK[cell[(s, q)]][1]}'>{MARK[cell[(s, q)]][0]}</td>"
                    for q in qids) + "</tr>")

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
<span class="badge">15 题逐题记录 + 5 种召回策略对照 · 判定基于检索，不依赖大模型</span>
</div>
</div></header>

<div class="livebar"><div class="inner">
<b>可交互的问答知识库（能真的提问）→</b>
<a href="{LIVE_URL}" target="_blank" rel="noopener">{LIVE_URL}</a>
<span class="note">本页是评估报告；要试用检索系统请点上链接，答案带「公司 + 年报页码」出处。</span>
</div></div>

<div class="wrap">

<h2>一句话结论</h2>
<div class="lead">
<b>点名公司要一个数字的问题，检索 9/9 全部命中；要跨实体聚合、排序、取极值的问题，
全局 top-k 检索 5 道只对 1 道（还是碰巧），且病根有两层——覆盖垄断 + 字段错位。</b><br>
对照实验证明：只换召回策略（按公司分层 + 关键章节保底）能把大部分失败修回来，
但代价是上下文涨 5 倍，且仍有题救不回来——<b>这类题的正解是先 ETL 成结构化宽表</b>；
要稳定口径、叙述方式才轮到微调。
</div>

<h2>数据规模</h2>
<div class="grid">
<div class="stat"><div class="n">15</div><div class="t">家公司年报</div></div>
<div class="stat"><div class="n">2,698</div><div class="t">解析页数</div></div>
<div class="stat"><div class="n">3,988</div><div class="t">还原表格（按行列）</div></div>
<div class="stat"><div class="n">9,762</div><div class="t">切块（带公司/章节/页码）</div></div>
<div class="stat"><div class="n">241万</div><div class="t">字</div></div>
</div>

<h2>一、15 道题的检索结果</h2>
<div class="card">
<p style="margin-top:0">判据：<b>标准答案需要的证据（哪家公司 + 哪个数字）是否进 top-8</b>。
标准答案全部回原文核对过，不是凭印象写的（我第一版金标准自己错了一题，见文末注脚）。</p>
<table>
<thead><tr><th>题型分组</th><th>题数</th><th>检索可答</th><th>表现与原因</th></tr></thead>
<tbody>{gtr}</tbody>
</table>
<p class="note"><b>合计 {n_ok}/{len(rows)} 题检索可答</b>（其中 1 题属碰巧，不算真会）。
逐题明细见下表。</p>
<table>
<thead><tr><th>题目</th><th>问题</th><th>标准答案</th><th>hybrid 召回块（前 4）</th><th>判定 / 错在哪</th></tr></thead>
<tbody>
{chr(10).join(trs)}
</tbody>
</table>
</div>

<h2>二、病根不在数据、也不在模型，在『哪些块能到答案面前』（两层）</h2>
<div class="card">
<h3 style="margin-top:0">第一层：覆盖垄断</h3>
<p>全局排序按语义相似度取块，名额会被和问句最像的那一两家吃掉。
最典型的是 Q10「茅台 vs 五粮液谁毛利率高」——<b>点名了两家公司，top-8 只覆盖 1/15 家，
top-40 也只有 2 家</b>，五粮液一块都没进候选。</p>
<h3>第二层：字段错位</h3>
<p>即使公司覆盖到了，含数字的块在该公司内部也排得很靠后（今世缘营收金额块排自家第 47 位），
因为语义检索先命中『讨论收入的段落』，而不是数字表。</p>
<h3>对照实验：只换召回策略，不动数据、不动模型权重</h3>
<table>
<thead><tr><th>召回策略</th><th>块数</th><th>覆盖</th><th>Q7</th><th>Q8</th><th>Q9</th><th>Q10</th><th>Q11</th><th>Q12</th></tr></thead>
<tbody>{mtr}</tbody>
</table>
<p class="note">够＝证据齐全，半＝只齐一部分，缺＝一项都没有。Q7 谁增速最快 / Q8 谁亏损 /
Q9 毛利率最高 / Q10 茅台vs五粮液 / Q11 三家营收谁高 / Q12 茅台三个数字。<br>
<b>加大 k（B）救不回覆盖垄断；按公司分层（C/D）立刻修好 Q10；再叠加『主要会计数据』
章节保底的元数据路由（E）才修好 Q7 与 Q12。代价是上下文从 8 块涨到 45 块；
而 Q8、Q11 五种策略都没救回来——这两题是真正的『跨实体聚合』。</b></p>
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
python 05_评估/评估_15题.py           # 15 题 × 3 种检索模式逐题记录
python 05_评估/对比_召回策略.py       # 5 种召回策略对照实验
python 06_结论/生成_一页结论.py       # 一页结论 PDF
python 07_展示/生成_展示页.py         # 本页面</pre>
<p class="note">年报 PDF（约 30MB）不进仓库，用上面的脚本一键重建；API key 只放 <code>.env</code>，
<code>.gitignore</code> 已排除。问答页面在无 key 时自动降级为纯检索模式。</p>
</div>

<h2>六、结论：同一批财报，什么该建库、什么该微调</h2>
<div class="card">
<h3 style="margin-top:0">① 建库（检索）</h3>
<p>点名了公司、要某个具体数字或事实的问题。答案是原文里的一行，检索能精确命中、还带得出处
（本次 9/9，最好第 1 位）；年报年年换，库可以增量重建。</p>
<h3>② 微调</h3>
<p>要稳定口径、术语、组织答案方式的问题（例如"用董秘的语气解释为什么下滑"）。检索只能抄原文，
学不会"先行业、再公司、后措施"的叙述结构——这类才是微调的位置。</p>
<h3>③ 两个都不对</h3>
<p>跨实体聚合、排序、取极值。本次 5 道跨公司题在全局 top-8 下只答对 1 道（还是碰巧）；
分层召回 + 章节路由能救回一部分，但 Q8/Q11 五种策略都没救回，且上下文要涨 5 倍。
把 15 家的关键指标抽成一张结构化宽表，再让模型读表，才是稳定解。</p>
</div>

<h2>注脚（诚实交代三件事）</h2>
<div class="card">
<p>① 第一版标准答案我自己就错了一题：Q8 只写了酒鬼酒 −3,395 万，漏了亏得更多的顺鑫农业 −1.52 亿，
是回原文核对才发现的。这反过来印证：人对数字的记忆不可靠，检索到的原文才可靠。</p>
<p>② 过程中修掉一个自己的实现缺陷：检索候选池大小原本跟着 top-k 变，导致同一个问题在
top-2 与 top-60 下排序不一致、分层召回取块不稳；改成固定取全部候选后结果才自洽
（03_索引/检索.py）。数字以修完后的为准。</p>
<p>③ 本次 15 题的判定<b>全部基于检索</b>（证据是否进 top-8、召回了哪些块、错在哪），
不依赖任何大模型；页面里的 LLM 答案生成只是可选增强，无 key 自动降级。</p>
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
    print(f"检索可答 {n_ok}/{len(rows)}")


if __name__ == "__main__":
    main()
