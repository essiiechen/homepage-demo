# 10_Vercel · 线上问答库：构建与自检脚本

**部署用的文件在仓库根目录**（`index.html` + `assets/`），不是这个目录——
因为 Vercel 是从 GitHub 仓库直接拉的，它只认仓库根，
把应用埋在子目录里就得多配一次 Root Directory，没必要。

这个目录只放**离线构建与验证脚本**：

```
仓库根/
  index.html          问答页面（纯静态，Vercel 直接托管）
  assets/             浏览器端检索需要的全部资源（见下）
  lib/retriever.py    BM25 + 元数据先验 + 分层召回（与本地检索逐条对齐，权威副本）
  data/*.pkl.gz       预构建索引，是 assets/ 的上游输入（不参与部署）

10_Vercel/            本目录：构建 + 自检，不参与部署
  预构建_索引.py       离线跑一次，生成仓库根 data/（分词 + idf + 文档长度）
  构建_前端索引.py     把 data/ 打包成浏览器能读的 assets/
  评估_线上策略.py     三种召回策略在 15 题上的实测（Python 口径）
  评估_前端版.js       同一份 retriever.js 在 Node 里跑 15 题（与浏览器同代码）
  自检_与本地一致.py    纯 Python BM25 == 本地 rank_bm25？必须 15/15
  题库_15.json         15 题（含 gold 判据）
```

## 为什么是纯静态

最早走的是 Vercel 的 Python Serverless 函数（`api/ask.py`），但构建一直失败：
Vercel dashboard 里的 **Framework Preset 会覆盖 `vercel.json`**，这个仓库原本是个人主页项目，
还在按旧框架构建。与其在 dashboard 里一轮轮猜，不如把检索搬到浏览器——
静态托管没有构建这一步，必定能上线，别人也一定能打开。
Serverless 版的代码（`api/ask.py`、`lib/answer.py`、`requirements*.txt`）已经删除，
只留下两边共用的 `lib/retriever.py`。

顺带一个当时也绕不过去的限制：Vercel Serverless 函数有包体上限，而 CPU 版 torch 一个就 180MB+，
sentence-transformers 装不上（装上也跑不动：无 GPU、冷启动还要加载模型）。
所以线上从一开始就只能用**纯 BM25**，这与静态化并不冲突。

这不是妥协，是实测之后的取舍：

| 口径 | 15 题检索可答 |
|---|---:|
| 本地 bge 神经 embedding + BM25 混合 | 9/15 |
| 线上纯 BM25 + 全局 top-8 | 8/15 |
| 线上纯 BM25 + 一刀切分层召回 | 3/15 |
| 线上纯 BM25 + **自适应策略（线上默认）** | **9/15** |

自适应策略（`lib/retriever.py` 的 `Retriever.auto`，前端同构实现在 `assets/retriever.js`）：
问题里点名了 1 家公司 → 全局 top-k；没点名或点名多家 → 分层召回（15 家各取 2 块 +「主要会计数据」章节保底）。
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

注意 `.vercel.app` 子域名是全平台唯一的：`homepage-demo.vercel.app` 是别人的站点，
你自己的项目域名会带一段后缀，以 dashboard 里显示的为准。

## 改完检索逻辑，先把三件事跑完再推

```bash
PYTHONPATH=tmp/site python3 10_Vercel/预构建_索引.py        # 重建 data/
PYTHONPATH=tmp/site python3 10_Vercel/构建_前端索引.py      # 重建 assets/
node 10_Vercel/评估_前端版.js                               # auto 应为 9/15
```

可选的两道保险（走 Python 口径，需要 tmp/site 依赖）：

```bash
PYTHONPATH=tmp/site python3 10_Vercel/自检_与本地一致.py     # 必须 15/15
PYTHONPATH=tmp/site python3 10_Vercel/评估_线上策略.py       # auto 应为 9/15
```

## 几个设计决定

- **倒排表压成扁平 array + varint/delta 编码**：浏览器里没有 pickle，
  所以打包成字节流（`assets/index.bin.gz` 1.16MB），用 `DataView` 按段偏移读。
  段之间做 4 字节对齐——TypedArray 要求对齐访问，否则 iOS Safari 会直接抛错。
- **正文按公司切 15 片按需拉取**：全文 462 万字（8.97MB）一次下完手机要等十几秒；
  但打分只需要倒排表，正文只在展示 top-k 时才用得上，所以只拉命中的那几家。
- **前端分词代替 jieba**：用索引词表 + jieba 高频词形（`assets/dict.txt.gz`，12 万条）
  做最大正向匹配。踩过的坑：只靠年报词表时「多少」不在词表里，被切成「多」+「少」两个高频字，
  噪声把 Q6 挤掉了——导出高频词形后恢复到 9/15。
- **idf 照抄 rank_bm25**：`ln(N-df+0.5) - ln(df+0.5)`，且负 idf 裁剪为 `0.25×平均 idf`。
  这两条改任一条，排序就和本地跑偏，`自检_与本地一致.py` 会抓到。
- **前后端同一份检索代码**：`assets/retriever.js` 既在浏览器里跑，也被 `评估_前端版.js`
  用 `require` 加载跑 15 题——所以实测数字就是线上数字，不是"另写一个差不多的"。
- **答案取年报原文摘录**：线上没有 LLM key（key 不能进仓库）。摘录每条带公司 + 页码，
  可回年报核对。
