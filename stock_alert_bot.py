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
        return

    for i in range(0, len(msg), 1900):
        try:
            requests.post(WEBHOOK_URL, json={"content": msg[i:i+1900]}, timeout=10)
        except Exception as e:
            print("Discord error:", e)


# =========================
# KR Universe
# =========================
def get_kr():
    try:
        tickers = stock.get_market_ticker_list(market="ALL")
        return [t + ".KS" for t in tickers]
    except:
        return []


# =========================
# US Universe (S&P + NASDAQ100)
# =========================
def get_us():
    try:
        sp = pd.read_html("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies")[0]["Symbol"]
        nasdaq = pd.read_html("https://en.wikipedia.org/wiki/Nasdaq-100")[4]["Ticker"]

        return list(set(sp.tolist() + nasdaq.tolist()))
    except:
        return []


# =========================
# Universe Router
# =========================
def get_universe():
    if MARKET_MODE == "KR":
        return get_kr()
    elif MARKET_MODE == "US":
        return get_us()
    elif MARKET_MODE == "ALL":
        return get_kr() + get_us()
    return []


# =========================
# 테마 필터 (US)
# =========================
def apply_theme_filter(tickers):

    themes = {
        "AI": {"NVDA","MSFT","GOOGL","META","AMZN","PLTR","AMD"},
        "SPACE": {"SPCE","RKLB","ASTS","LMT","NOC","RTX"},
        "POWER": {"CEG","VST","NEE","DUK","SO","ETN","GE"},
        "NUCLEAR": {"SMR","CCJ","BWXT","OKLO","NEE"},
        "COOLING": {"VRT","ANET","DELL","HPE","STX"},
        "DRONE": {"AVAV","KTOS","RTX","LMT","NOC","LHX"},
        "SEMICON": {"AVGO","INTC","TSM","QCOM","MU","ASML"},
        "CYBER": {"PANW","CRWD","ZS","FTNT"},
        "ENERGY_STORAGE": {"TSLA","LAC","QS","ALB","ENVX"},
        "CLOUD": {"AMZN","MSFT","GOOGL","SNOW","CRM","NOW"}
    }

    theme_set = set()
    for v in themes.values():
        theme_set |= v

    return list(set(tickers) & theme_set)


# =========================
# Signal
# =========================
def get_signal(df):

    if df is None or len(df) < 30:
        return None

    ma5 = df["Close"].rolling(5).mean()
    ma20 = df["Close"].rolling(20).mean()

    price = df["Close"].iloc[-1]

    if price > ma20.iloc[-1] and ma5.iloc[-1] > ma20.iloc[-1]:
        return "MAIN_BUY"
    elif price > ma20.iloc[-1]:
        return "ST_BUY"

    return None


# =========================
# Analyze
# =========================
def analyze(ticker):

    try:
        df = yf.download(ticker, period="6mo", interval="1d", progress=False)

        signal = get_signal(df)
        if not signal:
            return None

        price = df["Close"].iloc[-1]

        return f"""📊 {ticker}
🟢 신호: {signal}
💰 가격: {price:.2f}"""

    except:
        return None


# =========================
# RUN
# =========================
def run():

    universe = get_universe()

    if len(universe) == 0:
        send_discord(f"❌ Universe 없음 | MODE={MARKET_MODE}")
        return

    # US면 테마 필터 적용
    if MARKET_MODE in ["US", "ALL"]:
        universe = apply_theme_filter(universe)

    if len(universe) == 0:
        send_discord(f"📉 필터 후 종목 없음 | MODE={MARKET_MODE}")
        return

    results = []

    for t in universe[:80]:
        res = analyze(t)
        if res:
            results.append(res)
        time.sleep(0.2)

    if not results:
        send_discord(f"📉 신호 없음 | MODE={MARKET_MODE}")
    else:
        msg = "🚀 STOCK SCANNER\n\n" + "\n\n----------------\n\n".join(results)
        send_discord(msg)


if __name__ == "__main__":
    run()
