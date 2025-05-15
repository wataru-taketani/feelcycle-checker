#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
feelchecker.py – FEELCYCLE 予約監視 ＋ LINE 通知（Playwright, CF-wait）
"""
import asyncio, csv, os, re, sys, time
from typing import List, Tuple

import httpx
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright, TimeoutError as PWTimeout

# ───── 環境変数 ─────
FEEL_USER  = os.environ["FEEL_USER"]
FEEL_PASS  = os.environ["FEEL_PASS"]
SHEET_CSV  = os.environ["SHEET_CSV"]
CH_ACCESS  = os.environ["CH_ACCESS"]
DEBUG      = bool(int(os.getenv("DEBUG", "0")))

# ──────────────────────────────────────────
async def fetch_csv(url: str) -> List[Tuple[str, str, str, str]]:
    async with httpx.AsyncClient(follow_redirects=True, timeout=30.0) as c:
        r = await c.get(url)
    r.raise_for_status()
    if r.text.lstrip().startswith("<"):
        raise RuntimeError("CSV ではなく HTML が返りました。公開設定を確認してください。")

    rows = []
    for row in csv.reader(r.text.splitlines()):
        if not row or row[0].lower() == "date":
            continue
        date_, time_, studio_ = row[:3]
        user_id = row[3] if len(row) > 3 else ""
        rows.append((date_.strip(), time_.strip(), studio_.strip(), user_id.strip()))
    return rows

# ──────────────────────────────────────────
RESERVE_URL = "https://m.feelcycle.com/reserve?type=filter"
TIME_RE     = re.compile(r"\d{2}:\d{2}")

async def fetch_reserve_html() -> str:
    async with async_playwright() as p:
        browser = await p.webkit.launch(headless=True)
        ctx = await browser.new_context(
            user_agent=("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1")
        )
        page = await ctx.new_page()
        page.set_default_timeout(60_000)          # 60 s

        await page.goto("https://m.feelcycle.com/login")

        # ── Cloudflare 待ち受けループ ──
        for _ in range(12):                       # 最大 12×5 = 60 秒
            try:
                await page.wait_for_selector('input[name="email"], input[type="email"]', timeout=5_000)
                break
            except PWTimeout:
                if DEBUG: print("[DEBUG] CF チェックを待機中…")
        else:
            raise RuntimeError("Cloudflare チェックが解除されずタイムアウトしました。")

        # フォーム入力
        await page.fill('input[name="email"], input[type="email"]', FEEL_USER)
        await page.fill('input[name="password"]', FEEL_PASS)
        await page.click('button[type="submit"]')
        await page.wait_for_load_state("networkidle")

        await page.goto(RESERVE_URL)
        await page.wait_for_load_state("networkidle")
        html = await page.content()
        await browser.close()
        return html

# ──────────────────────────────────────────
def has_slot(html: str, date_: str, time_: str) -> bool:
    soup = BeautifulSoup(html, "html.parser")
    day_div = soup.find("div", class_="days", string=lambda s: s and date_ in s)
    if not day_div:
        if DEBUG: print(f"[DEBUG] {date_} 列なし")
        return False
    column = day_div.find_parent("div", class_="content") or day_div
    for lesson in column.find_all("div", class_=re.compile(r"seat-(available|reserved)")):
        m = TIME_RE.match(lesson.find("div", class_="time").get_text(strip=True))
        if m and m.group() == time_:
            return True
    return False

# ──────────────────────────────────────────
async def push_line(text: str, user_id: str):
    async with httpx.AsyncClient(timeout=10.0) as c:
        await c.post(
            "https://api.line.me/v2/bot/message/push",
            headers={"Authorization": f"Bearer {CH_ACCESS}", "Content-Type": "application/json"},
            json={"to": user_id, "messages": [{"type": "text", "text": text}]},
        )

# ──────────────────────────────────────────
async def main():
    watch = await fetch_csv(SHEET_CSV)
    print("監視対象:", len(watch))
    if not watch:
        return

    html = await fetch_reserve_html()
    sent = 0
    for d, t, s, uid in watch:
        if has_slot(html, d, t):
            msg = f"{d} {t} {s} が予約可能です！"
            print("通知:", msg)
            await push_line(msg, uid)
            sent += 1
        elif DEBUG:
            print(f"[DEBUG] {d} {t} 満席")

    print("通知件数:", sent)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        print("‼️   ERROR:", e)
        sys.exit(1)
