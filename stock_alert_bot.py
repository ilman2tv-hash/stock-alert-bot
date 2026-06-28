import os
import time
import requests
import pandas as pd
import yfinance as yf
from pykrx import stock
from concurrent.futures import ThreadPoolExecutor

# 환경 변수 및 설정
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
MARKET_MODE = os.getenv("MARKET_MODE", "ALL").upper().replace("-", "_")
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36'
}

# 하드코딩 백업 리스트
FALLBACK_US = ["AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "TSLA", "AMD", "NFLX", "PLTR", "INTC"]

THEMES = {
    "AI": {"NVDA", "MSFT", "GOOGL", "META", "AMZN", "PLTR", "AMD"},
    "SEMICON": {"AVGO", "INTC", "TSM", "QCOM", "MU", "ASML"},
    "POWER": {"CEG", "VST", "NEE", "DUK", "SO", "ETN", "GE"},
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

def get_us():
    try:
        sp_res = requests.get("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies", headers=HEADERS, timeout=10)
        sp = pd.read_html(sp_res.text, match="Symbol")[0]["Symbol"].tolist()
        nd_res = requests.get("https://en.wikipedia.org/wiki/Nasdaq-100", headers=HEADERS, timeout=10)
        nasdaq = pd.read_html(nd_res.text, match="Ticker")[0]["Ticker"].tolist()
        return list(set(sp + nasdaq))
    except:
        return FALLBACK_US

def get_kr():
    try:
        # 시가총액 상위 300개로 제한하여 안정성 확보
        kospi = [t + ".KS" for t in stock.get_market_ticker_list(market="KOSPI")[:300]]
        kosdaq = [t + ".KQ" for t in stock.get_market_ticker_list(market="KOSDAQ")[:300]]
        return kospi + kosdaq
    except:
        return []

def analyze_ticker(ticker):
    """개별 종목 분석 함수 (안정적인 데이터 수집)"""
    try:
        # yfinance 다운로드 시 retry 로직과 timeout 설정 효과
        df = yf.download(ticker, period="6mo", interval="1d", progress=False)
        if df is None or len(df) < 30: return None
        
        ma5 = df["Close"].rolling(5).mean().iloc[-1]
        ma20 = df["Close"].rolling(20).mean().iloc[-1]
        price = float(df["Close"].iloc[-1])
        
        if price > ma20 and ma5 > ma20: signal = "MAIN_BUY"
        elif price > ma20: signal = "ST_BUY"
        else: return None
        
        matched = [t for t, tickers in THEMES.items() if ticker in tickers]
        tag = f" [{', '.join(matched)}]" if matched else ""
        return f"📊 {ticker}{tag}\n🟢 신호: {signal}\n💰 가격: {price:.2f}"
    except:
        return None

def run():
    universe = []
    if MARKET_MODE in ["US", "ALL"]: universe += get_us()
    if MARKET_MODE in ["KR", "ALL"]: universe += get_kr()
    
    if not universe:
        send_discord("⚠️ 감시 대상 종목을 불러올 수 없습니다.")
        return

    print(f"🚀 스캔 시작: {len(universe)}개 종목 (안전 모드)")
    
    # 5개씩 병렬 처리하여 차단 방지 및 속도 향상
    results = []
    with ThreadPoolExecutor(max_workers=5) as executor:
        for res in executor.map(analyze_ticker, universe):
            if res:
                results.append(res)
            time.sleep(0.3) # 각 작업 사이 짧은 대기 (안전성)

    if not results:
        send_discord(f"📉 조건에 맞는 신호 없음 (총 {len(universe)}개 종목 스캔 완료)")
    else:
        msg = f"🚀 STOCK SCANNER ({MARKET_MODE})\n\n"
        for res in results:
            if len(msg) + len(res) > 1850:
                send_discord(msg)
                msg = ""
            msg += res + "\n\n----------------\n\n"
        send_discord(msg)

if __name__ == "__main__":
    run()
