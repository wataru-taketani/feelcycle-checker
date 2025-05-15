# 先頭に追加 ----------------------------------------------
DEBUG = os.environ.get("DEBUG", "0") == "1"
# ---------------------------------------------------------

# 3. 空き枠判定関数の最後だけ差し替え
        if m and m.group() == time_:
+            if DEBUG:
+                # レッスン枠の HTML をまるごと表示して確認
+                print("=== hit candidate =====================")
+                print(lesson.prettify()[:600])   # 600 文字だけ
+                print("======================================")
             return True

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
feelchecker.py – FEELCYCLE 予約監視 ＋ LINE 通知 〈完全版〉
──────────────────────────────────────────
● 使い方
   ・FEEL_USER   … FEELCYCLE ログイン用メールアドレス
   ・FEEL_PASS   … 同パスワード
   ・SHEET_CSV   … 公開状態（誰でも閲覧可）にした
                    Google スプレッドシート CSV URL
                    例: https://docs.google.com/spreadsheets/d/xxxxxxxxx/export?format=csv&gid=0
   ・CH_ACCESS   … LINE Bot のチャネルアクセストークン
──────────────────────────────────────────
スプレッドシート列
    date,time,studio,userId
例：5/17,13:45,銀座,Ud1382f998e1fa87c37b4f916600ff962
"""

import asyncio
import csv
import os
import re
from typing import List, Tuple

import httpx
from bs4 import BeautifulSoup

# ─────── 環境変数 ────────────────────────────
FEEL_USER = os.environ["FEEL_USER"]
FEEL_PASS = os.environ["FEEL_PASS"]
SHEET_CSV = os.environ["SHEET_CSV"]
CH_ACCESS = os.environ["CH_ACCESS"]

# ─────── 1. Google スプレッドシート（CSV）を取得 ─────
async def fetch_csv(url: str) -> List[Tuple[str, str, str, str]]:
    async with httpx.AsyncClient(follow_redirects=True, timeout=30.0) as client:
        r = await client.get(url)
    r.raise_for_status()

    # CSV ではなく HTML が返ってきた場合（シートが公開されていない等）
    if r.text.lstrip().startswith("<"):
        raise RuntimeError(
            "CSV ではなく HTML が返って来ました。\n"
            "スプレッドシートの公開設定を『リンクを知っている全員 ▶︎ 閲覧者』にしてください。"
        )

    rows: List[Tuple[str, str, str, str]] = []
    for row in csv.reader(r.text.splitlines()):
        if not row or row[0].strip().lower() == "date":        # ヘッダー行・空行をスキップ
            continue
        # date,time,studio,userId
        date_, time_, studio_ = row[:3]
        user_id = row[3] if len(row) > 3 else ""
        rows.append((date_.strip(), time_.strip(), studio_.strip(), user_id.strip()))
    return rows

# ─────── 2. FEELCYCLE 予約ページを取得 ──────────────
RESERVE_URL = "https://m.feelcycle.com/reserve?type=filter"

async def fetch_reserve_page() -> str:
    async with httpx.AsyncClient(follow_redirects=True, timeout=30.0) as client:
        # 1) ログイン
        await client.post(
            "https://m.feelcycle.com/login",
            data={"email": FEEL_USER, "password": FEEL_PASS},
        )
        # 2) 予約ページ HTML
        r = await client.get(RESERVE_URL)
    r.raise_for_status()
    return r.text

# ─────── 3. 予約 HTML から空き枠判定 ────────────────
TIME_RE = re.compile(r"\d{2}:\d{2}")

def has_slot(html: str, date_: str, time_: str) -> bool:
    """
    Parameters
    ----------
    date_ : str   "5/17"
    time_ : str   "13:45"  ← HTML の "13:45 - 14:30" の先頭と一致すれば OK
    """
    soup = BeautifulSoup(html, "html.parser")

    # ① 日付カラム .content を取得
    day_div = soup.find("div", class_="days", string=lambda s: s and date_ in s)
    if not day_div:
        return False
    column = day_div.find_parent("div", class_="content")
    if not column:
        return False

    # ② 「空きあり」のレッスン枠を列挙
    for lesson in column.find_all("div", class_=re.compile(r"seat-available")):
        time_div = lesson.find("div", class_="time")
        if not time_div:
            continue
        m = TIME_RE.match(time_div.get_text(strip=True))
        if m and m.group() == time_:
            return True
    return False

# ─────── 4. LINE Push ─────────────────────────────
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

# ─────── 5. メイン ───────────────────────────────
async def main():
    watch_list = await fetch_csv(SHEET_CSV)
    print(f"監視対象 {len(watch_list)} 件")
    if not watch_list:
        return

    html = await fetch_reserve_page()
    sent = 0

    for date_, time_, studio_, user_id in watch_list:
        if has_slot(html, date_, time_):
            msg = f"{date_} {time_} {studio_} が予約可能です！"
            await push_line(msg, user_id)
            sent += 1
            print("通知:", msg)

    print(f"通知件数: {sent}")

if __name__ == "__main__":
    asyncio.run(main())
