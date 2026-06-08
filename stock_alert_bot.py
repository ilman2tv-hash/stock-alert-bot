import yfinance as yf
import pandas as pd
import numpy as np
import requests
import os
import time
import json
import feedparser
from urllib.parse import quote
import re
from googletrans import Translator
from pykrx import stock
from datetime import datetime, timedelta

# ==========================================
# 1. 환경 설정 및 상수
# ==========================================
WEBHOOK_URL = os.getenv("WEBHOOK_URL") 
MARKET_MODE = os.getenv("MARKET_MODE", "US_OPTION") # "KR", "US", "ALL", "US_OPTION"

translator = Translator()

PERIOD = "1y"
INTERVAL = "1d"

KR_TOP_N = 400  # 코스피 200개 + 코스닥 200개
US_TOP_N = 600  # S&P 500 + 나스닥 100
WATCHLIST = [
    "SMR", "OKLO", "RKLB", "RDW", "ASTS", "CRWV",
    "NBIS", "IREN", "AAOI", "COHR", "HIMS", "LUNR"
]

# 슈퍼트렌드 설정
ST_ATR_PERIOD = 10
ST_FACTOR = 3.0

# ==========================================
# 2. 디스코드 및 뉴스 유틸리티
# ==========================================
def send_discord(message):
    if not WEBHOOK_URL or "http" not in WEBHOOK_URL:
        print("Webhook URL이 설정되지 않았습니다.")
        return
    try:
        chunks = [message[i:i + 1900] for i in range(0, len(message), 1900)]
        for chunk in chunks:
            requests.post(WEBHOOK_URL, json={"content": chunk}, timeout=15)
    except Exception as e:
        print(f"Discord 전송 실패: {e}")

def get_news_titles(stock_name, ticker):
    is_kr = ticker.endswith(".KS") or ticker.endswith(".KQ")
    query = quote(f"{stock_name} 주식" if is_kr else f"{stock_name} stock")
    lang = "ko-KR" if is_kr else "en-US"
    rss_url = f"https://news.google.com/rss/search?q={query}&hl={lang}&gl={lang[:2].upper()}&ceid={lang[:2].upper()}:{lang[3:]}"
    try:
        feed = feedparser.parse(rss_url)
        titles = []
        for entry in feed.entries[:3]:
            t = re.sub(r"\s-\s.+$", "", entry.title).strip()
            if not is_kr:
                try: 
                    t = translator.translate(t, dest="ko").text
                except Exception as e:
                    print(f"번역 오류 ({ticker}): {e}")
            titles.append(t)
        return titles if titles else ["관련 뉴스 없음"]
    except Exception as e:
        print(f"뉴스 검색 오류 ({ticker}): {e}")
        return ["뉴스 검색 오류"]

# ==========================================
# 3. 시장 상황 대시보드
# ==========================================
def get_market_status():
    try:
        data = yf.download(["SPY", "QQQ", "^VIX"], period="3mo", interval="1d", progress=False)
        if data.empty: return "📊 시장상황: 조회 실패"
        close = data["Close"].ffill().dropna()
        spy, qqq, vix = close["SPY"], close["QQQ"], close["^VIX"]
        
        spy_risk_on = spy.ewm(span=10).mean().iloc[-1] > spy.ewm(span=30).mean().iloc[-1]
        qqq_risk_on = qqq.ewm(span=10).mean().iloc[-1] > qqq.ewm(span=30).mean().iloc[-1]
        vix_val = float(vix.iloc[-1])
        
        score = int(spy_risk_on) + int(qqq_risk_on) + int(vix_val < 20)
        status = ["위험", "약세", "보통", "매우좋음"][score]
        vix_status = "안정" if vix_val < 20 else "경계" if vix_val < 30 else "위험"
        
        return (f"📊 시장상황: {status}\n"
                f"🇺🇸 미국시장: {'상승' if spy_risk_on else '약세'}\n"
                f"💻 기술주: {'상승' if qqq_risk_on else '약세'}\n"
                f"😱 공포지수: {vix_val:.2f} ({vix_status})")
    except Exception as e:
        print(f"시장 상황 조회 오류: {e}")
        return "📊 시장상황: 데이터 부족"

# ==========================================
# 4. 기관 옵션 매집(Smart Money) 감지 
# ==========================================
def get_high_conf_us_option_signal():
    try:
        data = yf.download(["SPY", "^VIX", "^VVIX", "^SKEW", "^PCCR"], period="3mo", interval="1d", progress=False)
        close = data["Close"].ffill() 
        
        spy = close["SPY"].dropna()
        vix = close["^VIX"].dropna()
        vvix = close["^VVIX"].dropna()
        skew = close["^SKEW"].dropna()
        pccr = close["^PCCR"].dropna()

        curr_spy = float(spy.iloc[-1])
        curr_vix = float(vix.iloc[-1])
        curr_vvix = float(vvix.iloc[-1])
        curr_skew = float(skew.iloc[-1])
        curr_pccr = float(pccr.iloc[-1])
        
        prev_spy = float(spy.iloc[-2])
        prev_vvix = float(vvix.iloc[-2])
        prev_pccr = float(pccr.iloc[-2])
        
        spy_ma20 = spy.rolling(window=20).mean().iloc[-1]
        pccr_ma20 = pccr.rolling(window=20).mean().iloc[-1]

        spy_change_pct = (curr_spy - prev_spy) / prev_spy * 100
        vvix_change_pct = (curr_vvix - prev_vvix) / prev_vvix * 100
        pccr_change_pct = (curr_pccr - prev_pccr) /
