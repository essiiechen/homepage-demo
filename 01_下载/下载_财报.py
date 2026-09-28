#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
下载白酒行业上市公司 2025 年年度报告全文 PDF（巨潮资讯网 cninfo.com.cn）。

用法：
    python 01_下载/下载_财报.py           # 增量下载（已存在且大小一致则跳过）
    python 01_下载/下载_财报.py --force    # 强制重下

产出：
    data/pdf/{代码}_{公司}_{报告期}.pdf
    data/manifest.json    每份报告的元信息（供后续切块与出处标注使用）

不依赖任何密钥，纯公开接口。PDF 不进 git（见 .gitignore）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
PDF_DIR = ROOT / "data" / "pdf"
PDF_DIR.mkdir(parents=True, exist_ok=True)

UA = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"),
    "Referer": "http://www.cninfo.com.cn/new/commonUrl?url=disclosure/list/notice",
    "Accept": "*/*",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "X-Requested-With": "XMLHttpRequest",
}
TOPSEARCH = "http://www.cninfo.com.cn/new/information/topSearch/query"
QUERY = "http://www.cninfo.com.cn/new/hisAnnouncement/query"
STATIC = "http://static.cninfo.com.cn"

# 白酒行业 A 股上市公司（代码, 简称, 交易所）
COMPANIES = [
    ("600519", "贵州茅台", "sse"),
    ("000858", "五粮液",   "szse"),
    ("000568", "泸州老窖", "szse"),
    ("600809", "山西汾酒", "sse"),
    ("002304", "洋河股份", "szse"),
    ("000596", "古井贡酒", "szse"),
    ("603369", "今世缘",   "sse"),
    ("000799", "酒鬼酒",   "szse"),
    ("600779", "水井坊",   "sse"),
    ("603198", "迎驾贡酒", "sse"),
    ("603589", "口子窖",   "sse"),
    ("600559", "老白干酒", "sse"),
    ("002646", "天佑德酒", "szse"),
    ("603919", "金徽酒",   "sse"),
    ("000860", "顺鑫农业", "szse"),
]

REPORT_YEAR = "2025"                 # 报告期（2025 年年报）
SE_DATE = "2026-01-01~2026-12-31"    # 2025 年年报的披露窗口
CATEGORY = "category_ndbg_szsh"      # 年度报告（沪深）

BAD_WORDS = ("摘要", "英文", "取消", "更正", "补充", "问询", "说明", "修订", "业绩快报", "预告")


def sess() -> requests.Session:
    s = requests.Session()
    s.headers.update(UA)
    return s


def get_orgid(s: requests.Session, code: str) -> str | None:
    """先查 orgId：巨潮的公告检索必须同时传 代码,orgId。"""
    try:
        r = s.post(TOPSEARCH, data={"keyWord": code, "maxNum": "10"}, timeout=20)
        r.raise_for_status()
        for item in r.json() or []:
            if item.get("code") == code:
                return item.get("orgId")
    except Exception as e:  # noqa: BLE001
        print(f"    ! topSearch 失败 {code}: {e}")
    return None


def find_annual_report(s: requests.Session, code: str, org_id: str, column: str) -> dict | None:
    """在该公司公告里挑出正文年报（排除摘要/英文/更正等）。"""
    payload = {
        "pageNum": 1, "pageSize": 50, "column": column, "tabName": "fulltext",
        "plate": "", "stock": f"{code},{org_id}", "searchkey": "", "secid": "",
        "category": CATEGORY, "trade": "", "seDate": SE_DATE,
        "sortName": "", "sortType": "", "isHLtitle": "true",
    }
    r = s.post(QUERY, data=payload, timeout=30)
    r.raise_for_status()
    anns = (r.json() or {}).get("announcements") or []
    cands = []
    for a in anns:
        title = a.get("announcementTitle", "")
        if REPORT_YEAR not in title or "年度报告" not in title:
            continue
        if any(w in title for w in BAD_WORDS):
            continue
        if not a.get("adjunctUrl"):
            continue
        cands.append(a)
    if not cands:
        return None
    # 同一份年报可能有多条（正文/附件版），取体积最大的那个
    cands.sort(key=lambda x: x.get("adjunctSize") or 0, reverse=True)
    return cands[0]


def download(s: requests.Session, url: str, dest: Path) -> bool:
    if dest.exists() and dest.stat().st_size > 20_000:
        return False
    r = s.get(url, timeout=120, stream=True)
    r.raise_for_status()
    tmp = dest.with_suffix(".part")
    with open(tmp, "wb") as f:
        for chunk in r.iter_content(65536):
            f.write(chunk)
    tmp.replace(dest)
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    s = sess()
    manifest = []
    for code, name, column in COMPANIES:
        print(f"[{code} {name}] ", end="", flush=True)
        try:
            org_id = get_orgid(s, code)
            if not org_id:
                print("取不到 orgId，跳过")
                continue
            time.sleep(0.6)
            a = find_annual_report(s, code, org_id, column)
            if not a:
                print(f"未找到 {REPORT_YEAR} 年年度报告")
                continue
            url = STATIC + "/" + a["adjunctUrl"].lstrip("/")
            dest = PDF_DIR / f"{code}_{name}_{REPORT_YEAR}年报.pdf"
            if args.force and dest.exists():
                dest.unlink()
            fresh = download(s, url, dest)
            ts = a.get("announcementTime")
            date = time.strftime("%Y-%m-%d", time.localtime(ts / 1000)) if ts else ""
            sha1 = hashlib.sha1(dest.read_bytes()).hexdigest()[:16] if dest.exists() else ""
            manifest.append({
                "code": code, "name": name, "exchange": column, "org_id": org_id,
                "title": a.get("announcementTitle"), "announce_date": date,
                "report_year": REPORT_YEAR, "url": url,
                "pdf": str(dest.relative_to(ROOT)), "bytes": dest.stat().st_size,
                "sha1": sha1,
            })
            print(f"{'新下载' if fresh else '已存在'} {dest.stat().st_size/1024:.0f}KB  {a.get('announcementTitle')}")
        except Exception as e:  # noqa: BLE001
            print(f"失败: {type(e).__name__} {e}")
        time.sleep(0.8)

    (ROOT / "data" / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n完成 {len(manifest)}/{len(COMPANIES)} 份，清单已写入 data/manifest.json")
    return 0 if manifest else 1


if __name__ == "__main__":
    sys.exit(main())
