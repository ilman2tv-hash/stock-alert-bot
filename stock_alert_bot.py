import os
import time
import requests
import pandas as pd
import numpy as np
import yfinance as yf
from pykrx import stock

WEBHOOK_URL = os.getenv("WEBHOOK_URL")
MARKET_MODE = os.getenv("MARKET_MODE", "KR").upper().replace("-", "_")

# =========================
# Discord
# =========================
def send_discord(msg):
    if not WEBHOOK_URL:
        print("NO WEBHOOK")
        print(msg) # 웹훅이 없을 때는 콘솔에 출력되도록 추가
        return

    try:
        requests.post(WEBHOOK_URL, json={"content": msg}, timeout=10)
    except Exception as e:
        print("Discord error:", e)


# =========================
# KR Universe
# =========================
def get_kr():
    try:
        tickers = stock.get_market_ticker_list(market="ALL")
        return [t + ".KS" for t in tickers]
    except Exception as e:
        print("KR 데이터 수집 실패:", e)
        return []


# =========================
# US Universe (S&P + NASDAQ100)
# =========================
def get_us():
    try:
        # 위키피디아 표 순서 변경에 대비하여 match 파라미터 사용 (안정성 강화)
        sp_tables = pd.read_html("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies", match="Symbol")
        sp = sp_tables[0]["Symbol"]

        nasdaq_tables = pd.read_html("https://en.wikipedia.org/wiki/Nasdaq-100", match="Ticker")
        nasdaq = nasdaq_tables[0]["Ticker"]

        return list(set(sp.tolist() + nasdaq.tolist()))
    except Exception as e:
        print("US 데이터 수집 실패:", e)
        return []


# =========================
# 테마 필터 (US 전용)
# =========================
def apply_theme_filter(tickers):
    themes = {
        "AI": {"NVDA","MSFT","GOOGL","META","AMZN","PLTR","AMD"},
        "SPACE": {"SPCE","RKLB","ASTS","LMT","NOC","RTX"},
        "POWER
