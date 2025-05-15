# ───── デバッグ：取得した CSV の 100 文字だけ表示 ─────
import httpx, os, sys, io, csv
csv_raw = httpx.get(os.getenv("SHEET_CSV"), timeout=10).text
print("▼CSV 先頭 100 文字▼")
print(csv_raw[:100].encode("unicode_escape").decode())
print("▲ここまで▲")

rows = list(csv.reader(io.StringIO(csv_raw)))
print("行数:", len(rows))
if rows:
    print("1 行目:", rows[0])
# ───── ここまで追加 ─────

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
feelchecker.py  (BOM・大小文字フリー版)
CSV ヘッダーが \ufeffdate,time,studio,userId でも正しく読める。
"""

import asyncio, csv, io, os, sys, httpx
from playwright.async_api import async_playwright, TimeoutError as PWTimeout

# ─── 環境変数 ───────────────────────────────────────────────────────────
NEEDED = ["FEEL_USER","FEEL_PASS","SHEET_CSV","CH_ACCESS"]
for k in NEEDED:
    if not os.getenv(k):
        print(f"環境変数 {k} がありません"); sys.exit(1)
FEEL_USER,FEEL_PASS,SHEET_CSV,CH_ACCESS = [os.getenv(k) for k in NEEDED]

# ─── 1. CSV 読み込み ───────────────────────────────────────────────────
def load_targets():
    try:
        csv_txt = httpx.get(SHEET_CSV, timeout=10).text
    except Exception as e:
        print("CSV 取得失敗:", e); sys.exit(1)

    rows = list(csv.DictReader(io.StringIO(csv_txt)))
    need = {"date","time","studio","userId"}
    out  = []
    for r in rows:
        # BOM・大小・空白を除去したキーを作る
        norm = {k.lstrip("\ufeff").strip().lower(): v.strip() for k,v in r.items()}
        if all(norm.get(c) for c in need):
            out.append({c: norm[c] for c in need})
    return out

TARGETS = load_targets()
if not TARGETS:
    print("監視対象が 0 件です。シートを確認してください。")
    sys.exit(0)

# ─── 2. LINE Push ────────────────────────────────────────────────────
async def notify(uid, msg):
    headers = {"Authorization": f"Bearer {CH_ACCESS}",
               "Content-Type": "application/json"}
    payload = {"to": uid, "messages":[{"type":"text","text": msg}]}
    async with httpx.AsyncClient() as cli:
        r = await cli.post("https://api.line.me/v2/bot/message/push",
                           headers=headers, json=payload)
    if r.status_code != 200:
        print("LINE エラー:", r.text)

# ─── 3. Playwright 本体 ─────────────────────────────────────────────
RESERVE_URL = "https://m.feelcycle.com/reserve?type=filter"

async def run():
    hits = 0
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
        page = await (await browser.new_context()).new_page()

        # ログイン
        await page.goto("https://m.feelcycle.com/login", timeout=30000)
        await page.fill('input[name="email"]', FEEL_USER)
        await page.fill('input[name="password"]', FEEL_PASS)
        await page.click('button[type="submit"]')
        try:
            await page.wait_for_selector("text=レッスン予約", timeout=15000)
        except PWTimeout:
            print("ログイン失敗"); return

        # 予約ページへ
        await page.goto(RESERVE_URL, timeout=30000)
        await page.wait_for_selector("#shujikuTab", timeout=10000)

        # ターゲットループ
        for t in TARGETS:
            date,time,studio,uid = t["date"],t["time"],t["studio"],t["userId"]

            # スタジオタブ
            for tab in await page.query_selector_all("#shujikuTab .address_item"):
                if studio in await (await tab.query_selector(".main")).inner_text():
                    if "active" not in (await tab.get_attribute("class")):
                        await tab.click(); await page.wait_for_timeout(400)
                    break
            else: continue

            # 日付列
            days = await page.eval_on_selector_all(
                "#scrollHeader .days","els=>els.map(e=>e.textContent.trim())")
            try:
                col = (await page.query_selector_all(".sc_list"))[
                      next(i for i,d in enumerate(days) if d.startswith(date))]
            except StopIteration:
                continue

            # 空席検索
            for card in await col.query_selector_all("div.lesson.seat-available"):
                txt = (await (await card.query_selector(".time")).inner_text()).strip()
                if txt.startswith(time):
                    await notify(uid, f"🔔 空き発見！\n{date} {txt} @{studio}")
                    hits += 1
                    break

        await browser.close()
    print("通知件数:", hits)

# ─── 4. 実行 ────────────────────────────────────────────────────────
if __name__ == "__main__":
    asyncio.run(run())
