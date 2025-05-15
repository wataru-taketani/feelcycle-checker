#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
feelchecker.py – FEELCYCLE 予約監視 ＋ LINE 通知
"""

import asyncio
import csv
import datetime as dt
import os
import re
from typing import List, Tuple

import httpx
from bs4 import BeautifulSoup   # requirements.txt に beautifulsoup4 を追加済み

FEEL_USER  = os.environ["FEEL_USER"]
FEEL_PASS  = os.environ["FEEL_PASS"]
SHEET_CSV  = os.environ["SHEET_CSV"]
CH_ACCESS  = os.environ["CH_ACCESS"]

# ──────────────────────────────────────────
# 1. Google スプレッドシートを CSV で取得
# ──────────────────────────────────────────
async def fetch_csv(url: str) -> List[Tuple[str, str, str, str]]:
    async with httpx.AsyncClient(follow_redirects=True, timeout=30.0) as client:
        r = await client.get(url)
    r.raise_for_status()

    # HTML を誤って受け取ったときはエラーにする
    if r.text.lstrip().startswith("<"):
        raise RuntimeError(
            "CSV ではなく HTML が返って来ました。\n"
            "シートの共有設定『リンクを知っている全員▶︎閲覧者』と "
            "URL（/export?format=csv&gid=…）を確認してください。"
        )

    rows = []
    for row in csv.reader(r.text.splitlines()):
        if len(row) < 4 or row[0].startswith("#"):   # 空行／コメント行はスキップ
            continue
        rows.append(tuple(cell.strip() for cell in row[:4]))
    return rows            # [(date,time,studio,userId), …]

# ──────────────────────────────────────────
# 2. FEELCYCLE 予約ページをスクレイピング
# ──────────────────────────────────────────
FEEL_URL = "https://m.feelcycle.com/reserve?type=filter"

async def fetch_reserve_page() -> str:
    async with httpx.AsyncClient(follow_redirects=True, timeout=30.0) as client:
        # ログイン
        data = {
            "email": FEEL_USER,
            "password": FEEL_PASS,
        }
        await client.post("https://m.feelcycle.com/login", data=data)
        # 予約ページ取得（HTML）
        r = await client.get(FEEL_URL)
    r.raise_for_status()
    return r.text

def search_one(html: str, date_: str, time_: str, studio_: str) -> bool:
    """指定レッスンが seat-available or seat-reserved なら True"""
    soup = BeautifulSoup(html, "html.parser")

    # 日付カラムを探す（例: 5/17(金) → 5/17）
    date_label = re.sub(r"\(.*?\)", "", date_)   # “(金)”を除去
    day_div = soup.find("div", class_="days", string=lambda s: s and date_label in s)
    if not day_div:
        return False

    # その日付列（.content）内で時間・スタジオをチェック
    column = day_div.find_parent(class_="content")
    pattern = re.compile(rf"{re.escape(time_)}.*?{re.escape(studio_)}", re.S)
    lesson = column.find("div", class_=("seat-available", "seat-reserved"),
                         string=lambda s: s and pattern.search(s))
    return lesson is not None

# ──────────────────────────────────────────
# 3. LINE Notify（チャネルアクセストークン長期版）
# ──────────────────────────────────────────
async def push_line(msg: str, user_id: str) -> None:
    url = "https://api.line.me/v2/bot/message/push"
    headers = {
        "Authorization": f"Bearer {CH_ACCESS}",
        "Content-Type":  "application/json",
    }
    body = {
        "to": user_id,
        "messages": [{"type": "text", "text": msg}],
    }
    async with httpx.AsyncClient(timeout=10.0) as client:
        await client.post(url, headers=headers, json=body)

# ──────────────────────────────────────────
# 4. メイン
# ──────────────────────────────────────────
async def main() -> None:
    watch_list = await fetch_csv(SHEET_CSV)
    print(f"監視対象 {len(watch_list)} 件")

    if not watch_list:
        return

    html = await fetch_reserve_page()
    sent = 0

    for date_, time_, studio_, uid in watch_list:
        if search_one(html, date_, time_, studio_):
            msg = f"{date_} {time_} {studio_} が予約可！"
            await push_line(msg, uid)
            sent += 1
            print("通知:", msg)

    print(f"通知件数: {sent}")

if __name__ == "__main__":
    asyncio.run(main())
