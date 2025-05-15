#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
feelchecker.py  (4-列シート版)
-------------------------------------------------------------
Google スプレッドシート (CSV) の監視条件
   date | time | studio | userId
を読み取り、FEELCYCLE 予約サイトを自動巡回。
空席 (seat-available) を見つけたら、その行の userId へ
LINE Push 通知を送る。

・Playwright 1.44 系 / Python ≥ 3.10
・requirements.txt
    playwright==1.44.0
    httpx>=0.26
"""

import asyncio, csv, io, os, sys, httpx
from playwright.async_api import async_playwright, TimeoutError as PWTimeout

# ─────── 0. 環境変数（GitHub Secrets で設定） ──────────────────────────
FEEL_USER = os.getenv("FEEL_USER")          # FEELCYCLE ID
FEEL_PASS = os.getenv("FEEL_PASS")          # FEELCYCLE パスワード
SHEET_CSV = os.getenv("SHEET_CSV")          # 公開 CSV URL
CH_ACCESS = os.getenv("CH_ACCESS")          # LINE チャネルアクセストークン(長期)

if not all([FEEL_USER, FEEL_PASS, SHEET_CSV, CH_ACCESS]):
    print("環境変数が不足しています。Secrets を確認してください。")
    sys.exit(1)

# ─────── 1. シート読み込み（date,time,studio,userId の4列） ──────────────
def load_targets():
    try:
        csv_txt = httpx.get(SHEET_CSV, timeout=10).text
    except Exception as e:
        print("シート取得失敗:", e); sys.exit(1)

    rows = list(csv.DictReader(io.StringIO(csv_txt)))
    # 必須4項目が埋まっている行だけ残す
    return [r for r in rows if all(r.get(k) for k in ("date","time","studio","userId"))]

TARGETS = load_targets()
if not TARGETS:
    print("監視行が 0 件です。シートを確認してください。")
    sys.exit(0)

# ─────── 2. LINE Push 通知関数（ユーザーごと） ───────────────────────────
async def notify(user_id: str, message: str):
    headers = {
        "Authorization": f"Bearer {CH_ACCESS}",
        "Content-Type":  "application/json"
    }
    payload = { "to": user_id,
                "messages": [{ "type": "text", "text": message }] }

    async with httpx.AsyncClient(timeout=10) as cli:
        r = await cli.post("https://api.line.me/v2/bot/message/push",
                           headers=headers, json=payload)
    if r.status_code != 200:
        print("LINE通知エラー:", r.text)

# ─────── 3. Playwright メイン処理 ──────────────────────────────────────
RESERVE_URL = "https://m.feelcycle.com/reserve?type=filter"

async def run():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
        context = await browser.new_context()
        page    = await context.new_page()

        # 3-A  ログイン
        await page.goto("https://m.feelcycle.com/login", timeout=30000)
        await page.fill('input[name="email"]', FEEL_USER)
        await page.fill('input[name="password"]', FEEL_PASS)
        await page.click('button[type="submit"]')
        try:
            await page.wait_for_selector("text=レッスン予約", timeout=15000)
        except PWTimeout:
            print("ログインに失敗しました。ID/PW を確認してください。"); return

        # 3-B  予約ページ表示
        await page.goto(RESERVE_URL, timeout=30000)
        await page.wait_for_selector("#shujikuTab", timeout=10000)

        # 3-C  監視行を順にチェック
        for t in TARGETS:
            date   = t["date"].strip()        # 例: 5/22
            time   = t["time"].strip()        # 例: 11:30
            studio = t["studio"].strip()      # 例: 銀座
            userId = t["userId"].strip()

            # (1) スタジオタブを選択
            tabs = await page.query_selector_all("#shujikuTab .address_item")
            for tab in tabs:
                name = await (await tab.query_selector(".main")).inner_text()
                if studio in name:
                    if "active" not in (await tab.get_attribute("class")):
                        await tab.click()
                        await page.wait_for_timeout(800)  # 軽く待つ
                    break
            else:
                print(f"スタジオ '{studio}' が見つかりません"); continue

            # (2) 日付列を見つける
            days = await page.eval_on_selector_all(
                "#scrollHeader .days",
                "els => els.map(e => e.textContent.trim())")
            try:
                idx = next(i for i,d in enumerate(days) if d.startswith(date))
            except StopIteration:
                # その週に日付が無い
                continue

            col = (await page.query_selector_all(".sc_list"))[idx]

            # (3) 空席カードを探す
            cards = await col.query_selector_all("div.lesson.seat-available")
            for card in cards:
                t_elem  = await card.query_selector(".time")
                time_txt = (await t_elem.inner_text()).strip()  # "11:30 - 12:15"
                if time_txt.startswith(time):
                    message = f"🔔 空き発見！\n{date} {time_txt} @{studio}"
                    await notify(userId, message)
                    break   # 1件見つけたら次ターゲットへ

        await browser.close()

# ─────── 4. エントリーポイント ────────────────────────────────────────
if __name__ == "__main__":
    asyncio.run(run())
