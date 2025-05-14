#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FEELCYCLE 予約サイトを巡回し、
Google スプレッドシート(csv) で指定した日付・時間・スタジオに
空席( seat-available )が出たら LINE にプッシュ通知する。
"""

import asyncio, csv, io, os, sys, httpx
from datetime import datetime
from playwright.async_api import async_playwright, TimeoutError as PWTimeout

# ─── 0. 環境変数（GitHub Secrets で設定） ──────────────────────────────
USER_ID   = os.getenv("USER_ID")            # LINE push の宛先
CH_ACCESS = os.getenv("CH_ACCESS")          # LINE チャネルアクセストークン
FEEL_USER = os.getenv("FEEL_USER")          # FEELCYCLE ID
FEEL_PASS = os.getenv("FEEL_PASS")          # FEELCYCLE PW
SHEET_CSV = os.getenv("SHEET_CSV")          # 公開CSV URL

# ─── 1. Google シート（CSV）を取得 → list(dict) に変換 ────────────────
def load_targets():
    """CSV の列: date | time | studio"""
    try:
        csv_txt = httpx.get(SHEET_CSV, timeout=10).text
    except Exception as e:
        print("CSV取得失敗:", e); sys.exit(1)

    rows = list(csv.DictReader(io.StringIO(csv_txt)))
    # 空行を除外
    return [r for r in rows if r.get("date") and r.get("time") and r.get("studio")]

TARGETS = load_targets()
if not TARGETS:
    print("監視対象が 0 件です。シートを確認してください。")
    sys.exit(0)

# ─── 2. 通知関数（LINE push）───────────────────────────────────────────
async def notify(msg):
    json_body = {"to": USER_ID, "messages": [{"type": "text", "text": msg}]}
    headers   = {"Authorization": f"Bearer {CH_ACCESS}",
                 "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=10) as cli:
        r = await cli.post("https://api.line.me/v2/bot/message/push",
                           headers=headers, json=json_body)
    if r.status_code != 200:
        print("LINE通知エラー:", r.text)

# ─── 3. Playwright メイン処理 ────────────────────────────────────────
RESERVE_URL = "https://m.feelcycle.com/reserve?type=filter"

async def run():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True,
                                          args=["--no-sandbox"])
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
            print("ログイン失敗。ID/PW を確認してください。"); return

        # 3-B 予約ページへ（ログイン後は自動遷移）
        await page.goto(RESERVE_URL, timeout=30000)
        await page.wait_for_selector("#shujikuTab", timeout=10000)

        # 3-C 監視ターゲットをループ
        for t in TARGETS:
            date   = t["date"].strip()      # 例: 5/22
            time   = t["time"].strip()      # 例: 11:30
            studio = t["studio"].strip()    # 例: 銀座

            # (1) スタジオタブをアクティブに
            tabs = await page.query_selector_all("#shujikuTab .address_item")
            for tab in tabs:
                name = await (await tab.query_selector(".main")).inner_text()
                if studio in name:
                    if "active" not in (await tab.get_attribute("class")):
                        await tab.click()
                        await page.wait_for_timeout(1000)
                    break
            else:
                print(f"スタジオ '{studio}' が見つかりません"); continue

            # (2) 日付列を取得（ヘッダーから位置を探す）
            days = await page.eval_on_selector_all(
                "#scrollHeader .days", "els=>els.map(e=>e.textContent.trim())")
            try:
                idx = next(i for i, d in enumerate(days) if d.startswith(date))
            except StopIteration:
                print(f"{studio} に {date} の列なし"); continue

            col = (await page.query_selector_all(".sc_list"))[idx]

            # (3) 空席カード検索
            cards = await col.query_selector_all("div.lesson.seat-available")
            for card in cards:
                t_elem  = await card.query_selector(".time")
                time_txt = (await t_elem.inner_text()).strip()
                if time_txt.startswith(time):
                    await notify(f"🔔 空き発見！\n{date} {time_txt} @{studio}")
                    break  # 1マッチで通知して次ターゲットへ

        await browser.close()

# ─── 4. エントリーポイント ─────────────────────────────────────────
if __name__ == "__main__":
    asyncio.run(run())
