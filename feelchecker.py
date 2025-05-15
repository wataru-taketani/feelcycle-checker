#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
feelchecker.py – FEELCYCLE 予約監視 ＋ LINE 通知
"""

import asyncio
import csv
import os
import re
from typing import List, Tuple

import httpx
from bs4 import BeautifulSoup

FEEL_USER  = os.environ["FEEL_USER"]
FEEL_PASS  = os.environ["FEEL_PASS"]
SHEET_CSV  = os.environ["SHEET_CSV"]
CH_ACCESS  = os.environ["CH_ACCESS"]

# ──────────────────────────────────────────
# 1. Google スプレッドシート（CSV）を取得
# ──────────────────────────────────────────
async def fetch_csv(url: str) -> List[Tuple[str, str, str]]:
    async with httpx.AsyncClient(follow_redirects=True, timeout=30.0) as client:
        r = await client.get(url)
    r.raise_for_status()

    if r.text.lstrip().startswith("<"):
        raise RuntimeError("CSV ではなく HTML が返って来ました。シート公開設定を確認してください。")

    rows = []
    for row in csv.reader(r.text.splitlines()):
        if not row or row[0].lower() == "date":      # ヘッダー行・空行をスキップ
            continue
        # date,time,studio,userId  → studio は今は使わない
        date_, time_, studio_ = row[:3]
        user_id = row[3] if len(row) > 3 else ""
        rows.append((date_.strip(), time_.strip(), studio_.strip(), user_id.strip()))
    return rows

# ──────────────────────────────────────────
# 2. FEELCYCLE 予約ページを取得
# ──────────────────────────────────────────
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

# ──────────────────────────────────────────
# 3. 予約 HTML から “○時×分の枠に空きがあるか” 判定
# ──────────────────────────────────────────
TIME_RE = re.compile(r"\d{2}:\d{2}")

def has_slot(html: str, date_: str, time_: str) -> bool:
    """
    date_  : "5/17"
    time_  : "13:45"   ← HTML の "13:45 - 14:30" の先頭と一致すれば OK
    """
    soup = BeautifulSoup(html, "html.parser")

    # ① 日付列 .content を取得
    day_div = soup.find("div", class_="days", string=lambda s: s and date_ in s)
    if not day_div:
        return False
    column = day_div.find_parent("div", class_="content")
    if not column:
        return False

    # ② 空き or 予約済みのレッスン枠を列挙
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
