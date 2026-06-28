import os
import time
import requests
import pandas as pd
import numpy as np
import yfinance as yf
from pykrx import stock

WEBHOOK_URL = os.getenv("WEBHOOK_URL")
MARKET_MODE = os.getenv("MARKET_MODE", "ALL").upper().replace("-", "_") # 기본 모드를 ALL로 변경

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
}

THEMES = {
    "AI": {"NVDA", "MSFT", "GOOGL", "META", "AMZN", "PLTR", "AMD"},
    "SPACE": {"SPCE", "RKLB", "ASTS", "LMT", "NOC", "RTX"},
    "POWER": {"CEG", "VST", "NEE", "DUK", "SO", "ETN", "GE"},
    "NUCLEAR": {"SMR", "CCJ", "BWXT", "OKLO", "NEE"},
    "COOLING": {"VRT", "ANET", "DELL", "HPE", "STX"},
    "DRONE": {"AVAV", "KTOS", "RTX", "LMT", "NOC", "LHX"},
    "SEMICON": {"AVGO", "INTC", "TSM", "QCOM", "MU", "ASML"},
    "CYBER": {"PANW", "CRWD", "ZS", "FTNT"},
    "ENERGY_STORAGE": {"TSLA", "LAC", "QS", "ALB", "ENVX"},
    "CLOUD": {"AMZN", "MSFT", "GOOGL", "SNOW", "CRM", "NOW"}
}

def send_discord(msg):
    if not WEBHOOK_URL:
        print(msg)
        return
    try:
        requests.post(WEBHOOK_URL, json={"content": msg}, timeout=10)
    except Exception as e:
        print("Discord error:", e)

# 1. 미국 종목 수집 (S&P 500 + NASDAQ 100)
def get_us():
    try:
        sp_res = requests.get("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies", headers=HEADERS)
        sp = pd.read_html(sp_res.text, match="Symbol")[0]["Symbol"].tolist()
        nd_res = requests.get("https://en.wikipedia.org/wiki/Nasdaq-100", headers=HEADERS)
        nasdaq = pd.read_html(nd_res.text, match="Ticker")[0]["Ticker"].tolist()
        return list(set(sp + nasdaq))
    except:
        return []

# 2. 한국 종목 수집 (전체)
def get_kr():
    try:
        kospi = [t + ".KS" for t in stock.get_market_ticker_list(market="KOSPI")]
        kosdaq = [t + ".KQ" for t in stock.get_market_ticker_list(market="KOSDAQ")]
        return kospi + kosdaq
    except:
        return []

def get_signal(df):
    if df is None or len(df) < 30: return None
    ma5 = df["Close"].rolling(5).mean().iloc[-1]
    ma20 = df["Close"].rolling(20).mean().iloc[-1]
    price = float(df["Close"].iloc[-1])
    if price > ma20 and ma5 > ma20: return "MAIN_BUY"
    elif price > ma20: return "ST_BUY"
    return None

def analyze(ticker):
    try:
        df = yf.download(ticker, period="6mo", interval="1d", progress=False)
        signal = get_signal(df)
        if not signal: return None
        
        price = float(df["Close"].iloc[-1])
        # 테마 태그 확인 (미국주식만)
        tag = ""
        if ticker in THEMES.values(): # 혹시라도 테마 리스트에 포함된 경우
             matched = [t for t, tickers in THEMES.items() if ticker in tickers]
             tag = f" [{', '.join(matched)}]"
        
        return f"📊 {ticker}{tag}\n🟢 신호: {signal}\n💰 가격: {price:.2f}"
    except:
        return None

def run():
    # 모드에 따라 유니버스 구성
    universe = []
    if MARKET_MODE in ["US", "ALL"]: universe += get_us()
    if MARKET_MODE in ["KR", "ALL"]: universe += get_kr()
    
    if not universe:
        send_discord("❌ 감시 대상 종목을 불러올 수 없습니다.")
        return

    results = []
    for t in universe:
        res = analyze(t)
        if res: results.append(res)
        time.sleep(0.2) 

    if not results:
        send_discord(f"📉 조건에 맞는 신호 없음 | MODE={MARKET_MODE}")
    else:
        # 메시지 쪼개서 발송
        msg = f"🚀 STOCK SCANNER ({MARKET_MODE})\n\n"
        for res in results:
            if len(msg) + len(res) > 1850:
                send_discord(msg)
                msg = ""
            msg += res + "\n\n----------------\n\n"
        send_discord(msg)

if __name__ == "__main__":
    run()
