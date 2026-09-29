#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""给问答页面截图（交付要的"页面截图"）。

页面现在是纯静态前端版，file:// 下 fetch 会被 CORS 拦掉，所以脚本自己起一个
本地 HTTP 服务（端口 8811），跑完再关掉——不需要先手动启动任何东西。

用法： PYTHONPATH=tmp/site python3 06_结论/截图.py
输出： 06_结论/截图_00_首页.png / 截图_01_单公司题.png / 截图_02_跨公司题.png
      截图_03_跨公司题_全局top8.png
"""
from __future__ import annotations

import functools
import http.server
import os
import socketserver
import threading
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "06_结论"
PORT = 8811
# 浏览器路径的坑踩过两次，方向相反：
#   本机有两处 playwright——系统 Python 3.9 的那份要 chromium_headless_shell-1223
#   （在 ~/Library/Caches/ms-playwright），项目 tmp/ 环境那份要 1243（在 tmp/pw）。
# 所以这里不写死：只有显式设了 PLAYWRIGHT_BROWSERS_PATH 才覆盖，
# 否则让当前这个 Python 的 playwright 去自己默认的缓存里找。
PW = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
if PW:
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = PW

SHOTS = [
    dict(name="截图_01_单公司题", q="五粮液2025年酒类产品的毛利率是多少？",
         strategy="auto", topk=8),
    dict(name="截图_02_跨公司题", q="这15家白酒公司里，2025年营业收入同比增长最快的是哪一家？增速是多少？",
         strategy="auto", topk=8),
    dict(name="截图_03_跨公司题_全局top8", q="这15家白酒公司里，2025年营业收入同比增长最快的是哪一家？增速是多少？",
         strategy="global", topk=8),
]


def serve() -> tuple[socketserver.TCPServer, threading.Thread]:
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(ROOT))
    socketserver.TCPServer.allow_reuse_address = True
    httpd = socketserver.TCPServer(("127.0.0.1", PORT), handler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    return httpd, t


def main() -> None:
    httpd, _ = serve()
    try:
        with sync_playwright() as p:
            b = p.chromium.launch()
            pg = b.new_page(viewport={"width": 1400, "height": 1150}, device_scale_factor=2)
            pg.goto(f"http://127.0.0.1:{PORT}/", wait_until="load", timeout=60000)

            # 索引是在浏览器里解压 + 解析的，等它真正就绪再截图
            pg.wait_for_function(
                "() => {const t=document.querySelector('#boot');"
                "return t && (t.textContent.includes('索引就绪')||t.textContent.includes('失败'));}",
                timeout=60000)
            boot = pg.inner_text("#boot")
            print("索引状态:", boot)
            if "失败" in boot:
                raise SystemExit("索引加载失败，先修 assets/ 再截图")
            pg.wait_for_timeout(800)
            pg.screenshot(path=str(OUT / "截图_00_首页.png"))
            print("✓ 截图_00_首页.png")

            for s in SHOTS:
                pg.fill("#q", s["q"])
                pg.select_option("#strategy", s["strategy"])
                pg.fill("#topk", str(s["topk"]))
                pg.click("#go")
                try:
                    pg.wait_for_function(
                        "() => {const t=document.querySelector('#ansState');"
                        "return t && !t.textContent.includes('检索中') && t.textContent!=='待提问';}",
                        timeout=60000)
                except Exception:
                    print(f"  ! {s['name']} 等待超时，仍截图")
                pg.wait_for_timeout(1200)
                pg.screenshot(path=str(OUT / f"{s['name']}.png"))
                print(f"✓ {s['name']}.png  ｜ {pg.inner_text('#meta')}")
                time.sleep(0.5)

            b.close()
        print("\n全部截图完成 →", OUT)
    finally:
        httpd.shutdown()


if __name__ == "__main__":
    main()
