#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
给问答页面截图（交付要的"页面截图"）。

需要先启动服务： python 04_问答/app.py --port 8791
用法： python 06_结论/截图.py
输出： 06_结论/截图_01_单公司题.png  06_结论/截图_02_跨公司题_全局top8.png
      06_结论/截图_03_跨公司题_分层召回.png
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "06_结论"
URL = "http://127.0.0.1:8791/"
PW = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", str(ROOT / "tmp" / "pw"))
# 光算出路径没用：launch() 只认环境变量。不设就会去 ~/Library/Caches/ms-playwright
# 找默认版本，跟本项目安装的 playwright 版本号对不上（要 1243，缓存里只有 1237）。
os.environ["PLAYWRIGHT_BROWSERS_PATH"] = PW
UNCHECK_LLM = os.environ.get("SHOT_LLM", "0") != "1"   # SHOT_LLM=1 时保留 LLM 生成

SHOTS = [
    dict(name="截图_01_单公司题", q="五粮液2025年酒类产品的毛利率是多少？",
         strategy="global", topk=8, wait=9000),
    dict(name="截图_02_跨公司题_全局top8", q="这15家白酒公司里，2025年营业收入同比增长最快的是哪一家？增速是多少？",
         strategy="global", topk=8, wait=12000),
    dict(name="截图_03_跨公司题_分层召回", q="这15家白酒公司里，2025年营业收入同比增长最快的是哪一家？增速是多少？",
         strategy="stratified", topk=8, wait=12000),
]


def main():
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={"width": 1400, "height": 1150}, device_scale_factor=2)
        pg.goto(URL, wait_until="networkidle", timeout=60000)
        pg.wait_for_timeout(1200)
        pg.screenshot(path=str(OUT / "截图_00_首页.png"))
        print("✓ 截图_00_首页.png")

        for s in SHOTS:
            pg.fill("#q", s["q"])
            pg.select_option("#strategy", s["strategy"])
            pg.fill("#topk", str(s["topk"]))
            # 免费模型每日 50 次额度有限：评估先跑完，截图用纯检索模式
            # （当天额度充足时，把 UNCHECK_LLM 改成 False 即可拍到 LLM 答案）
            if UNCHECK_LLM:
                pg.uncheck("#usellm")
            pg.click("#go")
            # 等答案出现（最多 60s，免费模型有时慢）
            try:
                pg.wait_for_function(
                    "() => {const t=document.querySelector('#ansState');"
                    "return t && !t.textContent.includes('检索中') && t.textContent!=='待提问';}",
                    timeout=60000)
            except Exception:
                print(f"  ! {s['name']} 等待超时，仍截图")
            pg.wait_for_timeout(1500)
            pg.screenshot(path=str(OUT / f"{s['name']}.png"))
            meta = pg.inner_text("#meta")
            print(f"✓ {s['name']}.png  ｜ {meta}")
            time.sleep(1)

        b.close()
    print("\n全部截图完成 →", OUT)


if __name__ == "__main__":
    main()
