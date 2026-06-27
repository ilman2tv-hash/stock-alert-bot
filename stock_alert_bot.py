import yfinance as yf
import pandas as pd
import numpy as np
import requests
import os
import time
from datetime import datetime, timedelta, timezone

# 1. 설정
WEBHOOK_URL = os.getenv("WEBHOOK_URL")

def send_discord(message):
    if not WEBHOOK_URL: return
    try:
        chunks = [message[i:i + 1900] for i in range(0, len(message), 1900)]
        for chunk in chunks: requests.post(WEBHOOK_URL, json={"content": chunk}, timeout=15)
    except Exception as e: print(f"Discord 전송 실패: {e}")

# 2. 1단계: 미국 우량주 600개 차트 깔때기 필터
def get_candidate_tickers():
    try:
        # S&P500 + Nasdaq100
        sp500 = pd.read_html("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies")[0]['Symbol'].tolist()
        nasdaq100 = pd.read_html("https://en.wikipedia.org/wiki/Nasdaq-100")[4]['Ticker'].tolist()
        tickers = list(set(sp500 + nasdaq100))[:600]
        
        data = yf.download(tickers, period="30d", group_by='ticker', progress=False)
        candidates = []
        for t in tickers:
            try:
                df = data[t].dropna()
                if len(df) < 20: continue
                curr_close = df["Close"].iloc[-1]
                ma20 = df["Close"].rolling(20).mean().iloc[-1]
                # 추세가 살아있거나 바닥권인 종목 필터링
                if curr_close < ma20 * 1.15: candidates.append(t)
            except: continue
        return candidates
    except: return []

# 3. 2단계: 옵션(OI) 정밀 분석 및 세력 목표가 포착
def analyze_options(ticker):
    try:
        tk = yf.Ticker(ticker)
        if not tk.options: return None
        
        # 가장 가까운 옵션 만기일 사용
        chain = tk.option_chain(tk.options[0])
        calls, puts = chain.calls.fillna(0), chain.puts.fillna(0)
        
        call_oi = calls["openInterest"].sum()
        put_oi = puts["openInterest"].sum()
        if call_oi == 0 or put_oi == 0: return None
        
        oi_pcr = put_oi / call_oi
        
        # 세력 목표가(행사가) 찾기
        curr_price = tk.history(period="1d")["Close"].iloc[-1]
        otm_calls = calls[calls["strike"] > curr_price]
        if otm_calls.empty: return None
        
        best_call = otm_calls.loc[otm_calls["openInterest"].idxmax()]
        call_conc = (best_call["openInterest"] / call_oi) * 100
        
        # 투트랙 조건 (OI P/C 비율 기반)
        # 1. 눌림목 매집: PCR 0.5 미만(콜 압도)
        if oi_pcr < 0.5 and call_conc >= 20.0:
            return f"📈 [{ticker}] 세력 눌림목 매집\n🎯 목표가: ${best_call['strike']:.2f} (콜 집중도: {call_conc:.1f}%, P/C: {oi_pcr:.2f})"
        # 2. 지하실 반격: PCR 0.5 미만 + 강한 의지
        elif oi_pcr < 0.4 and call_conc >= 25.0:
            return f"🔥 [{ticker}] 지하실 반격 매집\n🎯 목표가: ${best_call['strike']:.2f} (콜 집중도: {call_conc:.1f}%, P/C: {oi_pcr:.2f})"
        
        return None
    except: return None

# 4. 메인 실행
if __name__ == "__main__":
    candidates = get_candidate_tickers()
    found = []
    for t in candidates[:50]: # 서버 부하 방지, 상위 50개 우선 스캔
        res = analyze_options(t)
        if res: found.append(res)
        time.sleep(0.5)
        
    msg = f"🚀 미국 우량주 세력 엑기스 발굴 ({len(candidates)}개 후보 중)\n\n"
    if found: msg += "\n\n".join(found)
    else: msg += "현재 조건에 맞는 세력 매집 종목 없음."
    send_discord(msg)
