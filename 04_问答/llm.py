#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
答案生成层：把召回的块喂给大模型，让它基于原文作答并标注出处。

- key 只从项目根的 .env 或 ~/.thesis_keys 读，绝不写进代码 / 仓库
- 拿不到 key 时自动降级为"纯检索模式"（直接展示召回块），保证离线也能跑
- 默认走 OpenRouter，模型可用环境变量 LLM_MODEL 覆盖
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
API_URL = "https://openrouter.ai/api/v1/chat/completions"

# 免费额度模型回退链（按顺序试，429/402 就换下一个）。
# 首选金融向的 ling-flash-fin，中文财报口径处理更稳；qwen/gemma 作备份。
FALLBACK_MODELS = [
    "inclusionai/ling-3.0-flash-fin:free",
    "qwen/qwen3.8-27b:free",
    "google/gemma-4-31b-it:free",
    "nvidia/nemotron-3-super-120b-a12b:free",
]
DEFAULT_MODEL = FALLBACK_MODELS[0]


def load_env() -> dict:
    """从项目根 .env（优先）或 ~/.thesis_keys 读环境变量。"""
    env = {}
    for p in (ROOT / ".env", Path.home() / ".thesis_keys"):
        if not p.exists():
            continue
        for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip()
            if k.startswith("export "):      # 兼容 `export KEY=...` 写法
                k = k[7:].strip()
            v = v.strip().strip('"').strip("'")
            if v:
                env.setdefault(k, v)
    return env


def get_key() -> str | None:
    return os.environ.get("OPENROUTER_API_KEY") or load_env().get("OPENROUTER_API_KEY")


def get_model() -> str:
    return os.environ.get("LLM_MODEL") or load_env().get("LLM_MODEL") or DEFAULT_MODEL


SYSTEM = """你是上市公司年报的检索问答助手。只依据提供的【检索材料】作答，不得使用外部知识。

硬性要求：
1. 每个事实性陈述后面必须标出来源，格式为 [公司名 年报 P页码]，页码用材料里给的页码。
2. 材料中互相冲突的数字，以"印刷页码/报表正文"为准，并在答案里点出冲突。
3. 材料里没有的内容，直接回答"材料中未提及"，绝对不要推测、不要编数字。
4. 涉及多公司比较时，先给结论再给逐家数据，并说明口径（如"酒类毛利率"与"综合毛利率"不同）。
5. 答案用简体中文，简洁，先一句话给结论，再列要点。不要复述问题。"""


def build_context(hits: list[dict], max_chars: int = 9000) -> str:
    parts, used = [], 0
    for h in hits:
        c = h["chunk"]
        tag = (f"[{c['name']}（{c['code']}）2025年报 "
               f"章节：{c['section']}{' / ' + c['subsection'] if c['subsection'] else ''} "
               f"P{c['page']}]")
        body = c["text"]
        if used + len(body) > max_chars and parts:
            break
        parts.append(f"{tag}\n{body}")
        used += len(body)
    return "\n\n----\n\n".join(parts)


def _call(model: str, messages: list[dict], key: str, timeout: int) -> str:
    payload = {"model": model, "messages": messages,
               "temperature": 0.1, "max_tokens": 1400}
    req = urllib.request.Request(
        API_URL, data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {key}",
                 "Content-Type": "application/json",
                 "HTTP-Referer": "https://github.com/essiiechen",
                 "X-Title": "finance-report-rag"},
        method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        d = json.loads(r.read().decode("utf-8"))
    return d["choices"][0]["message"]["content"].strip()


def answer(query: str, hits: list[dict], model: str | None = None,
           timeout: int = 90, tries: int = 2) -> dict:
    """返回 {ok, text, model, error}。失败时 ok=False，text 为空。

    依次尝试 FALLBACK_MODELS，每个模型遇到 429（限流）重试 tries 次。
    """
    key = get_key()
    if not key:
        return {"ok": False, "text": "", "model": None,
                "error": "未配置 OPENROUTER_API_KEY（.env 或 ~/.thesis_keys），已降级为纯检索模式"}
    models = [model] if model else [get_model()] + [
        m for m in FALLBACK_MODELS if m != get_model()]
    messages = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content":
            f"【检索材料】\n{build_context(hits)}\n\n【问题】{query}"},
    ]
    errors = []
    for m in models:
        for t in range(tries):
            try:
                return {"ok": True, "text": _call(m, messages, key, timeout),
                        "model": m, "error": None}
            except urllib.error.HTTPError as e:
                body = e.read().decode("utf-8", "ignore")[:200]
                errors.append(f"{m} HTTP {e.code}: {body}")
                if e.code == 429 and t + 1 < tries:
                    time.sleep(3)
                    continue
                break
            except Exception as e:  # noqa: BLE001
                errors.append(f"{m} {type(e).__name__}: {e}")
                break
    return {"ok": False, "text": "", "model": None, "error": " ｜ ".join(errors[:3])}
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content":
                f"【检索材料】\n{build_context(hits)}\n\n【问题】{query}"},
        ],
        "temperature": 0.1,
        "max_tokens": 1200,
    }
    req = urllib.request.Request(
        API_URL, data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {key}",
                 "Content-Type": "application/json",
                 "HTTP-Referer": "https://github.com/essiiechen",
                 "X-Title": "finance-report-rag"},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.loads(r.read().decode("utf-8"))
        txt = d["choices"][0]["message"]["content"].strip()
        return {"ok": True, "text": txt, "model": model, "error": None}
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "ignore")[:300]
        return {"ok": False, "text": "", "model": model, "error": f"HTTP {e.code}: {body}"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "text": "", "model": model, "error": f"{type(e).__name__}: {e}"}


# 出处引用 [xx P数字] 的提取，用于前端高亮与"答案是否有出处"自检
CITE_RE = re.compile(r"[\[【]([^\]【]{0,40}?)P(\d+)[\]】]")


def cites(text: str) -> list[tuple[str, int]]:
    return [(m.group(1).strip(), int(m.group(2))) for m in CITE_RE.finditer(text)]


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(ROOT / "03_索引"))
    from 检索 import Index
    idx = Index.load(tag=os.environ.get("IDX_TAG", "bge"))
    q = sys.argv[1] if len(sys.argv) > 1 else "贵州茅台2025年营业总收入是多少"
    hits = idx.search(q, topk=8)
    print(f"[召回 {len(hits)} 块] 模型 {get_model()} key={'有' if get_key() else '无'}")
    r = answer(q, hits)
    print("OK" if r["ok"] else "降级", r.get("error") or "")
    print(r["text"][:1500])
