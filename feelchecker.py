#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
feelchecker.py – FEELCYCLE 予約監視 + LINE Push
"""

import asyncio, csv, os, re, textwrap
from typing import List, Tuple

import httpx
from bs4 import BeautifulSoup   # ログイン失敗検出用に依然として使用

FEEL_USER = os.environ["FEEL_USER"]
FEEL_PASS = os.environ["FEEL_PASS"]
SHEET_CSV = os.environ["SHEET_CSV"]
CH_ACCESS = os.environ["CH_ACCESS"]
DEBUG     = bool(os.getenv("DEBUG"))

# ──────────────────────────────────────────
# 1) Google スプレッドシート（CSV）
# ──────────────────────────────────────────
async def fetch_csv(url: str) -> List[Tuple[str,str,str,str]]:
    async with httpx.AsyncClient(follow_redirects=True, timeout=30) as c:
        r = await c.get(url)
    r.raise_for_status()

    if r.text.lstrip().startswith("<"):
        raise RuntimeError(
            "CSV ではなく HTML が返って来ました。シートの公開設定を再確認してください。"
        )

    rows: List[Tuple[str,str,str,str]] = []
    for row in csv.reader(r.text.splitlines()):
        if not row or row[0].lower() == "date":
            continue
        date_, time_, studio_ = row[:3]
        user_id = row[3] if len(row) > 3 else ""
        rows.append((date_.strip(), time_.strip(), studio_.strip(), user_id.strip()))
    return rows

# ──────────────────────────────────────────
# 2) FEELCYCLE 予約ページ
# ──────────────────────────────────────────
LOGIN_URL   = "https://m.feelcycle.com/login"
RESERVE_URL = "https://m.feelcycle.com/reserve?type=filter"

async def fetch_reserve_html() -> str:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_6) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
        )
    }
    async with httpx.AsyncClient(headers=headers, follow_redirects=True, timeout=30) as c:
        # ログイン
        resp = await c.post(LOGIN_URL, data={"email": FEEL_USER, "password": FEEL_PASS})
        resp.raise_for_status()

        # 予約ページ
        r = await c.get(RESERVE_URL)
    r.raise_for_status()

    # ログイン失敗検知（ログインフォームが残っている場合）
    if BeautifulSoup(r.text, "lxml").find("form", attrs={"action": re.compile("login")}):
        raise RuntimeError("ログインに失敗しました。FEEL_USER / FEEL_PASS を確認してください。")

    if DEBUG:
        print("── HTML head (800 B) ─────────────────────────")
        print(textwrap.shorten(r.text, width=800, placeholder=" … "))
        print("──────────────────────────────────────────")

    return r.text

# ──────────────────────────────────────────
# 3) 枠の空き判定（列に依存せず全文検索）
# ──────────────────────────────────────────
TIME_RE = re.compile(r"\d{2}:\d{2}")

def has_slot(html: str, date_: str, time_: str) -> bool:
    """
    * HTML 内で『date_ 5/17』が見つかり、その“後ろ”2000 文字以内に
      『time_ 13:45 -』があれば「その枠が存在」
    * さらにその近辺に `seat-available` があれば【空きあり】
      `seat-reserved` だけなら【満席】
    """
    # ① date_ の位置を探す
    for m in re.finditer(re.escape(date_), html):
        segment = html[m.end() : m.end() + 2000]        # date_ から 2000 B 先まで
        # ② time_ があるか
        t = re.search(re.escape(time_) + r"\s*-\s*\d{2}:\d{2}", segment)
        if not t:
            continue
        around = segment[t.start() : t.end()]           # time_ 付近
        # ③ available / reserved 判定
        if "seat-available" in around:
            return True     # 空きあり
    return False            # 見つからない or 満席

# ──────────────────────────────────────────
# 4) LINE Push
# ──────────────────────────────────────────
async def push_line(text: str, user_id: str):
    async with httpx.AsyncClient(timeout=10) as c:
        await c.post(
            "https://api.line.me/v2/bot/message/push",
            headers={
                "Authorization": f"Bearer {CH_ACCESS}",
                "Content-Type": "application/json",
            },
            json={"to": user_id, "messages":[{"type":"text", "text": text}]},
        )

# ──────────────────────────────────────────
# 5) メイン
# ──────────────────────────────────────────
async def main():
    watch = await fetch_csv(SHEET_CSV)
    print(f"監視対象: {len(watch)} 件")
    if not watch:
        return

    html = await fetch_reserve_html()
    sent = 0

    for date_, time_, studio_, user_id in watch:
        if has_slot(html, date_, time_):
            msg = f"{date_} {time_} {studio_} が予約可能です！"
            await push_line(msg, user_id)
            sent += 1
            print("通知:", msg)
        elif DEBUG:
            print(f"[DEBUG] {date_} {time_} には空き無し / 列見つからず")

    print(f"通知件数: {sent}")

if __name__ == "__main__":
    asyncio.run(main())
