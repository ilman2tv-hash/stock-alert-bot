import yfinance as yf
import pandas as pd
import numpy as np
import requests
import os
import feedparser
from urllib.parse import quote
import re
from googletrans import Translator
from pykrx import stock
from datetime import datetime, timedelta

# 환경 변수에서 설정 읽기
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "https://discord.com/api/webhooks/1503073387428446519/RNFbVwgOreGt4Hc708en5oh81_lEfG78YHb_PrUhgUGcin6CBu9Oslf-xIziv34ON1Ky")
MARKET_MODE = os.getenv("MARKET_MODE", "ALL")

translator = Translator()

PERIOD = "1y"
INTERVAL = "1d"
FAKE_BUY_BLOCK_PCT = 5.0
KR_TOP_N = 80
US_TOP_N = 80
SIGNAL_LOOKBACK_DAYS = 3

CRYPTO_TICKERS = {
    "BTC-USD": "비트코인", "ETH-USD": "이더리움", "SOL-USD": "솔라나",
    "BNB-USD": "BNB", "XRP-USD": "리플", "ADA-USD": "에이다",
    "AVAX-USD": "아발란체", "LINK-USD": "체인링크", "DOGE-USD": "도지코인", "SUI-USD": "수이",
}

def send_discord(message):
    if not WEBHOOK_URL or "http" not in WEBHOOK_URL:
        print("Webhook URL이 설정되지 않았습니다.")
        return
    requests.post(WEBHOOK_URL, json={"content": message})

def get_news_titles(stock_name, ticker):
    is_kr_stock = ticker.endswith(".KS") or ticker.endswith(".KQ")
    if is_kr_stock:
        query = quote(f"{stock_name} 주식")
        rss_url = f"https://news.google.com/rss/search?q={query}&hl=ko&gl=KR&ceid=KR:ko"
    else:
        query = quote(f"{stock_name} stock")
        rss_url = f"https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"

    try:
        feed = feedparser.parse(rss_url)
        news_titles = []
        seen = set()
        for entry in feed.entries[:5]:
            title = re.sub(r"\s-\s.+$", "", entry.title).strip()
            if not is_kr_stock:
                try: title = translator.translate(title, dest="ko").text
                except: pass
            if title and title not in seen:
                news_titles.append(title)
                seen.add(title)
            if len(news_titles) >= 3: break
        return news_titles if news_titles else ["관련 뉴스 없음"]
    except:
        return ["뉴스 검색 오류"]

def get_recent_kr_date():
    for i in range(10):
        target_date = (datetime.now() - timedelta(days=i)).strftime("%Y%m%d")
        try:
            df = stock.get_market_cap_by_ticker(target_date, market="KOSPI")
            if not df.empty: return target_date
        except: pass
    return datetime.now().strftime("%Y%m%d")

def get_kr_top_trading_value(top_n=80):
    date_str = get_recent_kr_date()
    result = {}
    for market, suffix in [("KOSPI", ".KS"), ("KOSDAQ", ".KQ")]:
        try:
            df = stock.get_market_cap_by_ticker(date_str, market=market)
            df = df.sort_values("거래대금", ascending=False).head(top_n // 2)
            for code in df.index:
                result[f"{code}{suffix}"] = stock.get_market_ticker_name(code)
        except: pass
    return result

def get_us_sp500_top_trading_value(top_n=80):
    try:
        url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
        table = pd.read_html(url)[0]
        symbols = table["Symbol"].str.replace(".", "-", regex=False).tolist()
        candidates = {}
        for symbol in symbols[:150]: # 속도를 위해 상위 150개만 샘플링 후 거래대금 정렬
            try:
                df = yf.download(symbol, period="5d", interval="1d", progress=False)
                if df.empty: continue
                last = df.iloc[-1]
                candidates[symbol] = float(last["Close"] * last["Volume"])
            except: continue
        sorted_items = sorted(candidates.items(), key=lambda x: x[1], reverse=True)[:top_n]
        return {symbol: symbol for symbol, _ in sorted_items}
    except: return {}

def build_tickers_by_mode():
    if MARKET_MODE == "KR":
        print("💡 모드: 국장(KOREA) 전용 스캔")
        return get_kr_top_trading_value(KR_TOP_N)
    elif MARKET_MODE == "US":
        print("💡 모드: 미장(USA) 전용 스캔")
        return get_us_sp500_top_trading_value(US_TOP_N)
    elif MARKET_MODE == "CRYPTO":
        print("💡 모드: 코인(CRYPTO) 전용 스캔")
        return CRYPTO_TICKERS
    else:
        print("💡 모드: 전체(ALL) 스캔")
        all_t = get_kr_top_trading_value(KR_TOP_N)
        all_t.update(get_us_sp500_top_trading_value(US_TOP_N))
        all_t.update(CRYPTO_TICKERS)
        return all_t

# --- 보조 지표 및 시그널 계산 함수 (기존 로직 유지) ---
def crossover(a, b): return (a > b) & (a.shift(1) <= b.shift(1))
def crossunder(a, b): return (a < b) & (a.shift(1) >= b.shift(1))

def calculate_signals(df):
    df = df.copy()
    # 지표 계산
    df["ma20"] = df["Close"].rolling(20).mean()
    df["ma5"] = df["Close"].rolling(5).mean()
    df["obv"] = (np.sign(df["Close"].diff()) * df["Volume"]).fillna(0).cumsum()
    df["obvUp"] = df["obv"] > df["obv"].shift(1)
    
    # 일목균형표
    df["tenkan"] = (df["High"].rolling(9).max() + df["Low"].rolling(9).min()) / 2
    df["kijun"] = (df["High"].rolling(26).max() + df["Low"].rolling(26).min()) / 2
    df["senkouA"] = ((df["tenkan"] + df["kijun"]) / 2).shift(26)
    df["senkouB"] = ((df["High"].rolling(52).max() + df["Low"].rolling(52).min()) / 2).shift(26)
    df["cloudTop"] = df[["senkouA", "senkouB"]].max(axis=1)
    
    # ADX 계산 (간략화)
    plus_dm = (df["High"].diff().clip(lower=0))
    minus_dm = (-df["Low"].diff().clip(lower=0))
    tr = pd.concat([(df["High"]-df["Low"]), abs(df["High"]-df["Close"].shift(1)), abs(df["Low"]-df["Close"].shift(1))], axis=1).max(axis=1)
    atr = tr.rolling(14).mean()
    plus_di = 100 * (plus_dm.rolling(14).mean() / atr)
    minus_di = 100 * (minus_dm.rolling(14).mean() / atr)
    dx = 100 * abs(plus_di - minus_di) / (plus_di + minus_di)
    df["adx"] = dx.rolling(14).mean()
    df["diplus"], df["diminus"] = plus_di, minus_di

    # 시그널 로직
    df["mainBuy"] = crossover(df["diplus"], df["diminus"]) & df["obvUp"] & (df["Close"] > df["ma20"])
    df["mainSell"] = crossunder(df["diplus"], df["diminus"]) | crossunder(df["Close"], df["ma20"])
    
    return df

# --- 메인 실행 루프 ---
TICKERS = build_tickers_by_mode()
all_latest_signals = []

for ticker, name in TICKERS.items():
    try:
        df = yf.download(ticker, period=PERIOD, interval=INTERVAL, progress=False)
        if df.empty or len(df) < 30: continue
        
        df = calculate_signals(df)
        last_rows = df.tail(SIGNAL_LOOKBACK_DAYS)
        
        for idx, row in last_rows.iterrows():
            if row["mainBuy"] or row["mainSell"]:
                all_latest_signals.append({
                    "ticker": ticker, "name": name, "date": idx,
                    "close": row["Close"], "signal": "BUY" if row["mainBuy"] else "SELL"
                })
    except: continue

if all_latest_signals:
    msg = f"🚨 [{MARKET_MODE}] 시장 자동 스캔 결과\n"
    for s in all_latest_signals:
        news = "\n".join([f"• {t}" for t in get_news_titles(s['name'], s['ticker'])])
        msg += f"\n[{s['signal']}] {s['name']} ({s['ticker']})\n가격: {float(s['close']):.2f}\n뉴스:\n{news}\n"
    send_discord(msg)
else:
    send_discord(f"✅ [{MARKET_MODE}] 스캔 완료: 특이 신호 없음")
