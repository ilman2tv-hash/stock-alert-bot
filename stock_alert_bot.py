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

# --- 환경 설정 ---
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
MARKET_MODE = os.getenv("MARKET_MODE", "ALL") # KR, US, US_OPTION 중 선택

translator = Translator()

# 데이터 조회 설정
PERIOD = "1y"
INTERVAL = "1d"
KR_TOP_N = 80
US_TOP_N = 80
SIGNAL_LOOKBACK_DAYS = 2
FAKE_BUY_BLOCK_PCT = 5.0
ST_ATR_PERIOD = 10
ST_FACTOR = 3.0

def send_discord(message):
    if not WEBHOOK_URL or "http" not in WEBHOOK_URL:
        print("Webhook URL 설정 오류")
        return
    try:
        chunks = [message[i:i + 1900] for i in range(0, len(message), 1900)]
        for chunk in chunks:
            requests.post(WEBHOOK_URL, json={"content": chunk}, timeout=15)
        print("Discord 전송 완료")
    except Exception as e:
        print("Discord 전송 오류:", e)

def get_market_status():
    try:
        data = yf.download(["SPY", "QQQ", "^VIX"], period="3mo", interval="1d", progress=False)
        if data.empty: return "📊 시장상황: 조회 실패"
        close = data["Close"]
        spy, qqq, vix = close["SPY"].dropna(), close["QQQ"].dropna(), close["^VIX"].dropna()
        
        spy_risk_on = spy.ewm(span=10, adjust=False).mean().iloc[-1] > spy.ewm(span=30, adjust=False).mean().iloc[-1]
        qqq_risk_on = qqq.ewm(span=10, adjust=False).mean().iloc[-1] > qqq.ewm(span=30, adjust=False).mean().iloc[-1]
        vix_val = float(vix.iloc[-1])
        
        score = int(spy_risk_on) + int(qqq_risk_on) + int(vix_val < 20)
        status = ["위험", "약세", "보통", "매우좋음"][score]
        return f"📊 시장상황: {status} | VIX: {vix_val:.2f}"
    except:
        return "📊 시장상황: 데이터 부족"

# --- [추가] 미국 옵션 고신뢰도 분석 로직 ---
def get_high_conf_us_option_signal():
    try:
        # SPY(가격), ^VIX(변동성), ^PCCR(수급) 삼박자 분석
        data = yf.download(["SPY", "^VIX", "^PCCR"], period="5d", interval="1d", progress=False)
        if data.empty: return None
        
        close = data["Close"]
        curr_pccr = float(close["^PCCR"].dropna().iloc[-1])
        curr_vix = float(close["^VIX"].dropna().iloc[-1])
        curr_spy = float(close["SPY"].dropna().iloc[-1])
        prev_spy = float(close["SPY"].dropna().iloc[-2])

        # 필터링: 1.9백만 원 자금을 지키기 위한 보수적/고신뢰도 조건
        # 상방: 콜옵션 폭증(0.6미만) + 공포지수 안정(20미만) + 지수 상승
        if curr_pccr < 0.60 and curr_vix < 20 and curr_spy > prev_spy:
            return f"🔥 **[미국 옵션 상방 신호]** 기관 콜옵션 매수 집중! (PCCR: {curr_pccr:.2f})"
        
        # 하방: 풋옵션 폭증(1.1초과) + 공포지수 상승(25초과) + 지수 하락
        elif curr_pccr > 1.10 and curr_vix > 25 and curr_spy < prev_spy:
            return f"🚨 **[미국 옵션 하방 주의]** 기관 풋옵션 대량 유입! (PCCR: {curr_pccr:.2f})"
            
        return None
    except:
        return None

# --- 기존 주식 스캔 함수들 (동일 유지) ---
def calculate_signals(df):
    # (사용자님의 기존 지표 계산 로직: DMI, Supertrend, MA, Cloud 등 동일)
    # 내용이 길어 생략하나 실제 파일에는 사용자님의 원본 코드를 그대로 넣으시면 됩니다.
    return df 

def build_tickers_by_mode():
    if MARKET_MODE == "KR": return get_kr_top_trading_value(KR_TOP_N)
    elif MARKET_MODE == "US": return get_us_sp500_top_trading_value(US_TOP_N)
    elif MARKET_MODE == "US_OPTION": return {} # 옵션모드는 종목스캔 안함
    return {}

# --- 메인 실행부 (Body) ---
if __name__ == "__main__":
    print(f"=== 실행 시작 (모드: {MARKET_MODE}) ===")
    market_status_text = get_market_status()

    # 모드별 분기 처리
    if MARKET_MODE == "US_OPTION":
        option_signal = get_high_conf_us_option_signal()
        if option_signal:
            msg = (
                f"🇺🇸 **미국 옵션 실시간 이상징후 알람**\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"{option_signal}\n"
                f"{market_status_text}\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"※ 기관 수급+지수방향+변동성 합산 신호"
            )
            send_discord(msg)
        else:
            print("신호 없음: 조건 충족 데이터가 없습니다.")

    else:
        # 기존 KR/US 주식 종목 스캔 로직 실행
        TICKERS = build_tickers_by_mode()
        # ... (기존 루프 및 신호 발견 로직 동일하게 수행) ...
        # (생략된 기존 코드를 여기에 그대로 두시면 됩니다.)

    print("=== 실행 종료 ===")
