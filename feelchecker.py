#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
feelchecker.py  (debug 版：ヒット件数を表示)
Google スプレッドシート
  date | time | studio | userId
を読み取り、空席があれば userId へ LINE Push。
"""

import asyncio, csv, io, os, sys, httpx
from playwright.async_api import async_playwright, TimeoutError as PWTimeout

# ─── 環境変数（GitHub Secrets） ──────────────────────────────────────
FEEL_USER = os.getenv("FEEL_USER")
FEEL_PASS = os.getenv("FEEL_PASS")
SHEET_CSV = os.getenv("SHEET_CSV")
CH_ACCESS = os.getenv("CH_ACCESS")

for k, v in {"FEEL_USER":FEEL_USER,"FEEL_PASS":FEEL_PASS,
             "SHEET_CSV":SHEET_CSV,"CH_ACCESS":CH_ACCESS}.items():
    if not v:
        print(f"環境変数 {k} がありません"); sys.exit(1)

# ─── 1. 監視リストをロード ────────────────────────────────────────────
def load_targets():
    csv_txt = httpx.get(SHEET_CSV, timeout=10).text
    rows = list(csv.DictReader(io.StringIO(csv_txt)))
    return [r for r in rows if all(r.get(k) for k in ("date","time","studio","userId"))]

TARGETS = load_targets()
if not TARGETS:
    print("監視対象がありません"); sys.exit(0)

# ─── 2. LINE Push ────────────────────────────────────────────────────
async def notify(user_id: str, message: str):
    headers = {"Authorization": f"Bearer {CH_ACCESS}",
               "Content-Type":  "application/json"}
    payload = {"to": user_id,
               "messages":[{"type":"text","text": message}]}
    async with httpx.AsyncClient(timeout=10) as cli:
        r = await cli.post("https://api.line.me/v2/bot/message/push",
                           headers=headers, json=payload)
    if r.status_code != 200:
        print("LINE送信エラー:", r.text)

# ─── 3. Playwright 本体 ─────────────────────────────────────────────
RESERVE_URL = "https://m.feelcycle.com/reserve?type=filter"

async def run():
    hits = 0   # 🔴 ヒット件数カウンタ

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
        context = await browser.new_context()
        page    = await context.new_page()

        # 3-A ログイン
        await page.goto("https://m.feelcycle.com/login", timeout=30000)
        await page.fill('input[name="email"]', FEEL_USER)
        await page.fill('input[name="password"]', FEEL_PASS)
        await page.click('button[type="submit"]')
        try:
            await page.wait_for_selector("text=レッスン予約", timeout=15000)
        except PWTimeout:
            print("ログイン失敗"); return

        # 3-B 予約ページ
        await page.goto(RESERVE_URL, timeout=30000)
        await page.wait_for_selector("#shujikuTab", timeout=10000)

        # 3-C 監視ループ
        for t in TARGETS:
            date, time, studio, userId = [t[k].strip() for k in
                                          ("date","time","studio","userId")]

            # スタジオタブ選択
            for tab in await page.query_selector_all("#shujikuTab .address_item"):
                if studio in await (await tab.query_selector(".main")).inner_text():
                    if "active" not in (await tab.get_attribute("class")):
                        await tab.click(); await page.wait_for_timeout(500)
                    break
            else: continue  # スタジオ見つからず

            # 日付列 index
            days = await page.eval_on_selector_all(
                "#scrollHeader .days",
                "els => els.map(e => e.textContent.trim())")
            try:
                col = (await page.query_selector_all(".sc_list"))[
                        next(i for i,d in enumerate(days) if d.startswith(date))]
            except StopIteration:
                continue  # その週に列なし

            # 空席検索
            for card in await col.query_selector_all("div.lesson.seat-available"):
                time_txt = (await (await card.query_selector(".time")).inner_text()).strip()
                if time_txt.startswith(time):
                    msg = f"🔔 空き発見！\n{date} {time_txt} @{studio}"
                    await notify(userId, msg)
                    hits += 1
                    break

        await browser.close()
    print("通知件数:", hits)   # 🔵 GitHub Actions ログに出力

# ─── 4. 実行 ──────────────────────────────────────────────────────
if __name__ == "__main__":
    asyncio.run(run())
