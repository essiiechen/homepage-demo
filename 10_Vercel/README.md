# 10_Vercel · 线上问答库：构建与自检脚本

**部署用的文件在仓库根目录**（`index.html` / `api/ask.py` / `lib/` / `data/`），
不是这个目录——因为 Vercel 是从 GitHub 仓库直接拉的，它只认仓库根，
把应用埋在子目录里就得多配一次 Root Directory，没必要。

这个目录只放**离线构建与验证脚本**，产物一律写到仓库根的 `data/`：

```
仓库根/
  index.html          问答页面（静态，Vercel 直接托管）
  api/ask.py          唯一的函数入口：POST /api/ask（纯标准库 WSGI，不依赖 flask）
  lib/retriever.py    BM25 + 元数据先验 + 分层召回（与本地检索逐条对齐）
  lib/answer.py       答案合成：有 key 走大模型，没 key 走年报原文摘录
  data/*.pkl.gz       预构建索引（chunks 2.2MB + 倒排表 1.7MB）
  vercel.json         函数配置（includeFiles 把 data/ 和 lib/ 打进函数）
  requirements.txt    只有 jieba —— Vercel 从仓库根装包

10_Vercel/            本目录：构建 + 自检，不参与部署
  预构建_索引.py       离线跑一次，生成仓库根 data/
  自检_与本地一致.py    线上结果 == 本地结果？必须 15/15
  评估_线上策略.py      三种召回策略在 15 题上的实测
```

## 为什么线上不能用 bge

Vercel 的 Serverless Function 有 **250MB 包体上限**，而 CPU 版 torch 一个就 180MB+，
sentence-transformers 装不上（装上也跑不动：无 GPU、冷启动还要加载模型）。

所以线上走**纯 BM25**。这不是妥协，是实测之后的取舍：

| 口径 | 15 题检索可答 |
|---|---:|
| 本地 bge 神经 embedding + BM25 混合 | 9/15 |
| 线上纯 BM25 + 全局 top-8 | 8/15 |
| 线上纯 BM25 + 一刀切分层召回 | 3/15 |
| 线上纯 BM25 + **自适应策略（线上默认）** | **9/15** |

自适应策略（`lib/retriever.py` 的 `Retriever.auto`）：问题里点名了 1 家公司 → 全局 top-k；
没点名或点名多家 → 分层召回（15 家各取 2 块 +「主要会计数据」章节保底）。
判据与 `05_评估/评估_15题.py` 完全相同，实测脚本 `评估_线上策略.py`。

**分层召回不能一刀切**——它是给"15 家里谁最高"这类题补覆盖度的，
用在单公司题上会把要找的那家挤下去（问"茅台营收"，分层下前 3 块全是别家），
所以才按"点名了几家公司"分流。这是 15 题逐条比出来的，不是拍脑袋。

## 部署

仓库已接 Vercel（GitHub 联动），**推到 main 就自动部署**，不用跑任何命令：

```bash
# 双击 08_上传包/一键推送到GitHub.command
```

部署完的网址在 Vercel 项目页看（这个项目原来叫 homepage-demo）。
拿到后请**关掉 WiFi 用手机流量**打开一次，那才是真的公网可访问。

## 本地先跑一遍

```bash
PYTHONPATH=tmp/site python3 api/ask.py     # 在仓库根跑，起在 8766
curl -s localhost:8766/api/health
curl -s -X POST localhost:8766/api/ask -H 'Content-Type: application/json' \
     -d '{"q":"贵州茅台2025年营业总收入是多少？","strategy":"auto","topk":5}'
```

改完检索逻辑，先把三件事跑完再推：

```bash
PYTHONPATH=tmp/site python3 10_Vercel/预构建_索引.py      # 重建 data/
PYTHONPATH=tmp/site python3 10_Vercel/自检_与本地一致.py   # 必须 15/15
PYTHONPATH=tmp/site python3 10_Vercel/评估_线上策略.py     # auto 应为 9/15
```

## 几个设计决定

- **数据预构建**：线上现分词 9762 块再建 BM25 要 5-8 秒，serverless 扛不住。
  预构建后冷启动只剩解压 + 反序列化（实测 0.04s），首次查询 ~0.2s。
- **倒排表压成扁平 array**（`pdoc`/`ptf`/`start`）：pickle 几百万个小 tuple 反序列化太慢，
  array 是从 buffer 直接还原。
- **idf 照抄 rank_bm25**：`ln(N-df+0.5) - ln(df+0.5)`，且负 idf 裁剪为 `0.25×平均 idf`。
  这两条改任一条，排序就和本地跑偏，`自检_与本地一致.py` 会抓到。
- **不用 flask**：WSGI 用标准库就能写，少一个装包环节就少一处部署失败的可能。
- **答案默认取原文摘录**：线上没有 LLM key（key 不能进仓库）。摘录每条带公司 + 页码，
  可回年报核对；在 Vercel 项目设置里加 `OPENROUTER_API_KEY` 环境变量就会自动切换成大模型作答。
