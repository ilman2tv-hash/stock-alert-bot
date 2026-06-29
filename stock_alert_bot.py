import os
import time
import requests
import pandas as pd
import yfinance as yf
import FinanceDataReader as fdr
from concurrent.futures import ThreadPoolExecutor

# 환경 변수 및 설정
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
MARKET_MODE = os.getenv("MARKET_MODE", "ALL").upper().replace("-", "_")
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36'
}

# 하드코딩 백업 리스트 및 종목명
FALLBACK_US = ["AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "TSLA", "AMD", "NFLX", "PLTR", "INTC"]
TICKER_NAMES = {
    "AAPL": "애플", "MSFT": "마이크로소프트", "NVDA": "엔비디아", "GOOGL": "구글",
    "AMZN": "아마존", "META": "메타", "TSLA": "테슬라", "AMD": "AMD",
    "NFLX": "넷플릭스", "PLTR": "팔란티어", "INTC": "인텔"
}

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

# [미국장] 종목명 매칭 로직
def get_us():
    global TICKER_NAMES
    try:
        sp_res = requests.get("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies", headers=HEADERS, timeout=10)
        sp_df = pd.read_html(sp_res.text, match="Symbol")[0]
        
        nd_res = requests.get("https://en.wikipedia.org/wiki/Nasdaq-100", headers=HEADERS, timeout=10)
        nd_df = pd.read_html(nd_res.text, match="Ticker")[0]
        
        for _, row in sp_df.iterrows():
            ticker = str(row.get("Symbol")).strip()
            name = str(row.get("Security")).strip()
            if ticker and name and ticker not in TICKER_NAMES: 
                TICKER_NAMES[ticker] = name
            
        for _, row in nd_df.iterrows():
            ticker = str(row.get("Ticker")).strip()
            name = str(row.get("Company")).strip()
            if ticker and name and ticker not in TICKER_NAMES: 
                TICKER_NAMES[ticker] = name
            
        return list(set(sp_df["Symbol"].tolist() + nd_df["Ticker"].tolist()))
    except:
        return FALLBACK_US

# [국장] 종목명 매칭 로직
def get_kr():
    global TICKER_NAMES
    try:
        df_krx = fdr.StockListing('KRX')
        top300 = df_krx.sort_values(by='Marcap', ascending=False).head(300)
        
        tickers = []
        for _, row in top300.iterrows():
            market_suffix = ".KS" if row['Market'] == 'KOSPI' else ".KQ"
            full_ticker = f"{row['Code']}{market_suffix}"
            tickers.append(full_ticker)
            
            # 국장 종목명 저장 (예: 삼성전자)
            TICKER_NAMES[full_ticker] = str(row['Name']).strip()
        return tickers
    except Exception as e:
        print(f"KR 데이터 수집 오류: {e}")
        return []

def analyze_ticker(ticker):
    try:
        df = yf.download(ticker, period="6mo", interval="1d", progress=False)
        if df.empty or len(df) < 30: return None
        
        close_data = df["Close"].iloc[:, 0] if len(df["Close"].shape) > 1 else df["Close"]
        
        ma5 = close_data.rolling(5).mean().iloc[-1]
        ma20 = close_data.rolling(20).mean().iloc[-1]
        price = float(close_data.iloc[-1])
        
        # 신호 메시지 한글화
        if price > ma20 and ma5 > ma20: 
            signal = "🔥 강력 매수 (이평선 정배열)"
        elif price > ma20: 
            signal = "🍏 단기 매수 (20일선 상회)"
        else: 
            return None
        
        matched = [t for t, tickers in THEMES.items() if ticker.split('.')[0] in tickers]
        tag = f" [{', '.join(matched)}]" if matched else ""
        
        stock_name = TICKER_NAMES.get(ticker, ticker)
        
        return f"📊 **{stock_name}** ({ticker}){tag}\n💬 신호: {signal}\n💰 현재가: {price:.2f}"
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
    
    results = []
    with ThreadPoolExecutor(max_workers=5) as executor:
        for res in executor.map(analyze_ticker, universe):
            if res:
                results.append(res)
            time.sleep(0.3) 

    if not results:
        send_discord(f"📉 조건에 맞는 신호 없음 (총 {len(universe)}개 종목 스캔 완료)")
    else:
        # 헤더 메시지 한글화
        msg = f"🚀 **주식 스캐너 알림 ({MARKET_MODE})**\n\n"
        for res in results:
            if len(msg) + len(res) > 1850:
                send_discord(msg)
                msg = ""
            msg += res + "\n\n----------------\n\n"
        send_discord(msg)

if __name__ == "__main__":
    run()
