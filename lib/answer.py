#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
答案合成：有 key 走大模型，没 key 走提取式摘要。

线上版默认没有 key（key 不能进仓库），所以提取式才是默认路径——这不是降级，
而是把"检索到了什么"原样交给用户：每条都带公司 + 页码，可以回年报原文核对。
布置环境变量 OPENROUTER_API_KEY 后自动切换成大模型作答。

本作业评的是检索本身，两条路径都不影响评估结论。
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request

API_URL = "https://openrouter.ai/api/v1/chat/completions"
FALLBACK_MODELS = [
    "inclusionai/ling-3.0-flash-fin:free",
    "qwen/qwen3.8-27b:free",
    "google/gemma-4-31b-it:free",
    "nvidia/nemotron-3-super-120b-a12b:free",
]

SYSTEM = (
    "你是财报问答助手。只能依据【检索材料】回答，不得使用外部知识。\n"
    "要求：\n"
    "1) 直接给数字和结论，不要复述问题；\n"
    "2) 每个数字后面用 [公司名 第X页] 标注出处，出处必须来自材料的标签；\n"
    "3) 材料里没有的就说'检索材料中没有'，不要编造；\n"
    "4) 涉及多家公司时用分点列出，不要只说一家。"
)


def get_key() -> str | None:
    return os.environ.get("OPENROUTER_API_KEY") or None


def get_model() -> str:
    return os.environ.get("LLM_MODEL") or FALLBACK_MODELS[0]


def has_key() -> bool:
    return bool(get_key())


# ---------------------------------------------------------------- 大模型的输入
def build_context(hits: list[dict], max_chars: int = 9000) -> str:
    parts, used = [], 0
    for h in hits:
        c = h["chunk"]
        tag = (f"[{c['name']}（{c['code']}）2025年报 "
               f"章节：{c['section']}{' / ' + c['subsection'] if c['subsection'] else ''} "
               f"第{c['page']}页]")
        body = c["text"]
        if used + len(body) > max_chars and parts:
            break
        parts.append(f"{tag}\n{body}")
        used += len(body)
    return "\n\n----\n\n".join(parts)


def _call(model: str, messages: list[dict], key: str, timeout: int = 60) -> str:
    payload = {"model": model, "messages": messages,
               "temperature": 0.1, "max_tokens": 1200}
    req = urllib.request.Request(
        API_URL, data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {key}",
                 "Content-Type": "application/json",
                 "HTTP-Referer": "https://github.com/essiiechen",
                 "X-Title": "baijiu-annual-report-rag"},
        method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        d = json.loads(r.read().decode("utf-8"))
    return d["choices"][0]["message"]["content"].strip()


def answer_llm(query: str, hits: list[dict]) -> dict:
    key = get_key()
    if not key:
        return {"ok": False, "text": "", "model": None, "error": "未配置 OPENROUTER_API_KEY"}
    models = [get_model()] + [m for m in FALLBACK_MODELS if m != get_model()]
    messages = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": f"【检索材料】\n{build_context(hits)}\n\n【问题】{query}"},
    ]
    errors = []
    for m in models[:2]:          # 线上有超时预算，最多试两个模型
        try:
            return {"ok": True, "text": _call(m, messages, key), "model": m, "error": None}
        except Exception as e:  # noqa: BLE001
            errors.append(f"{m} {type(e).__name__}: {str(e)[:120]}")
    return {"ok": False, "text": "", "model": None, "error": " ｜ ".join(errors)}


# ---------------------------------------------------------------- 提取式摘要
_SENT_SPLIT = re.compile(r"(?<=[。；；!?！？])\s*")


def answer_extractive(query: str, hits: list[dict], max_blocks: int = 4) -> str:
    """把最相关的几句原文摘出来——每条都带公司 + 页码，用户可回年报核对。"""
    from retriever import tokenize

    q_terms = {t for t in tokenize(query) if len(t) >= 2}
    lines = []
    used = 0
    for h in hits[:max_blocks]:
        c = h["chunk"]
        sents = [s.strip() for s in _SENT_SPLIT.split(c["text"]) if s.strip()]
        if not sents:
            sents = [c["text"][:200]]
        scored = []
        for s in sents:
            hit = sum(1 for t in q_terms if t in s)
            has_num = bool(re.search(r"\d", s))
            scored.append((hit + (0.5 if has_num else 0), -len(s), s))
        scored.sort(reverse=True)
        pick = [s for _, _, s in scored[:2]]
        body = " ".join(pick)
        if used + len(body) > 900 and lines:
            break
        used += len(body)
        sec = c["section"] + (f" / {c['subsection']}" if c["subsection"] else "")
        head = f"[{c['name']}（{c['code']}）第{c['page']}页"
        head += f" · {sec}]" if sec else "]"
        lines.append(f"{head}\n{body}")
    return "\n\n".join(lines)


def cites(text: str) -> list[list]:
    """从大模型答案里抽出 [公司 第X页] 形式的出处标记。"""
    out = []
    for m in re.finditer(r"\[([^\]]{2,40}?)\s*第?\s*(\d+)\s*页?\]", text):
        out.append([m.group(1).strip(), int(m.group(2))])
    return out
