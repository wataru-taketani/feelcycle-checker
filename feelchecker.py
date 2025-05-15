#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
feelchecker.py – FEELCYCLE 予約監視 ＋ LINE 通知（Playwright 版）
────────────────────────────────────────
環境変数（GitHub-Actions → Secrets）  
  FEEL_USER   : FEELCYCLE 登録メールアドレス  
  FEEL_PASS   : FEELCYCLE パスワード  
  SHEET_CSV   : 公開 CSV の URL  (…/export?format=csv&gid=0)  
  CH_ACCESS   : LINE Messaging API  チャネルアクセストークン  
  DEBUG       : 1 を入れるとデバッグログ多め  
"""
import asyncio, csv, os, re
from typing import List, Tuple

import httpx
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

FEEL_USER  = os.environ["FEEL_USER"]
FEEL_PASS  = os.environ["FEEL_PASS"]
SHEET_CSV  = os.environ["SHEET_CSV"]
CH_ACCESS  = os.environ["CH_ACCESS"]
DEBUG      = bool(int(os.getenv("DEBUG", "0")))

# ──────────────────────────────────────────
# 1. Google シート（CSV）を取得
# ──────────────────────────────────────────
async def fetch_csv(url: str) -> List[Tuple[str, str, str, str]]:
    async with httpx.AsyncClient(follow_redirects=True, timeout=30.0) as client:
        r = await client.get(url)
    r.raise_for_status()

    if r.text.lstrip().startswith("<"):
        raise RuntimeError(
            "CSV ではなく HTML が返りました。シートの公開設定を「リンクを知っている全員 ▶︎ 閲覧者」にしてください。"
        )

    rows: List[Tuple[str, str, str, str]] = []
    for row in csv.reader(r.text.splitlines()):
        if not row or row[0].lower() == "date":      # ヘッダー／空行 skip
            continue
        date_, time_, studio_ = row[:3]
        user_id = row[3] if len(row) > 3 else ""
        rows.append((date_.strip(), time_.strip(), studio_.strip(), user_id.strip()))
    return rows

# ──────────────────────────────────────────
# 2. Playwright で予約ページ HTML を取得
# ──────────────────────────────────────────
RESERVE_URL = "https://m.feelcycle.com/reserve?type=filter"
TIME_RE     = re.compile(r"\d{2}:\d{2}")

async def fetch_reserve_html() -> str:
    async with async_playwright() as p:
        browser = await p.webkit.launch(headless=True)
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
            )
        )
        page = await context.new_page()

        # ① ログイン画面へ
        await page.goto("https://m.feelcycle.com/login")
        await page.fill('input[name="email"]', FEEL_USER)
        await page.fill('input[name="password"]', FEEL_PASS)
        await page.click('button[type="submit"]')
        await page.wait_for_load_state("networkidle")

        # ② 予約ページへ遷移
        await page.goto(RESERVE_URL)
        await page.wait_for_load_state("networkidle")
        html = await page.content()
        await browser.close()
        return html

# ──────────────────────────────────────────
# 3. HTML から指定枠の空き判定
# ──────────────────────────────────────────
def has_slot(html: str, date_: str, time_: str) -> bool:
    soup = BeautifulSoup(html, "html.parser")

    # 日付列
    day_div = soup.find("div", class_="days", string=lambda s: s and date_ in s)
    if not day_div:
        if DEBUG:
            print(f"[DEBUG] {date_} の列が見つかりません")
        return False
    column = day_div.find_parent("div", class_="content")
    if not column:
        return False

    # 予約可 or 予約済み (= seat-available / seat-reserved)
    for lesson in column.find_all("div", class_=re.compile(r"seat-(available|reserved)")):
        time_div = lesson.find("div", class_="time")
        if not time_div:
            continue
        m = TIME_RE.match(time_div.get_text(strip=True))
        if m and m.group() == time_:
            return True
    return False

# ──────────────────────────────────────────
# 4. LINE Push
# ──────────────────────────────────────────
async def push_line(text: str, user_id: str):
    async with httpx.AsyncClient(timeout=10.0) as client:
        await client.post(
            "https://api.line.me/v2/bot/message/push",
            headers={
                "Authorization": f"Bearer {CH_ACCESS}",
                "Content-Type": "application/json",
            },
            json={
                "to": user_id,
                "messages": [{"type": "text", "text": text}],
            },
        )

# ──────────────────────────────────────────
# 5. メイン
# ──────────────────────────────────────────
async def main():
    watch_list = await fetch_csv(SHEET_CSV)
    print(f"監視対象: {len(watch_list)} 行")

    if not watch_list:
        return

    html = await fetch_reserve_html()
    sent = 0

    for date_, time_, studio_, user_id in watch_list:
        if has_slot(html, date_, time_):
            msg = f"{date_} {time_} {studio_} が予約可能です！"
            print("通知:", msg)
            await push_line(msg, user_id)
            sent += 1
        elif DEBUG:
            print(f"[DEBUG] {date_} {time_} は満席 / 列なし")

    print(f"通知件数: {sent}")

if __name__ == "__main__":
    asyncio.run(main())
