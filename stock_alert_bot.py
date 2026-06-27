import yfinance as yf
import pandas as pd
import numpy as np
import requests
import os
import time
import json
from datetime import datetime, timedelta, timezone

# ==========================================
# 1. 설정
# ==========================================
WEBHOOK_URL = os.getenv("WEBHOOK_URL") 

def send_discord(message):
    if not WEBHOOK_URL: return
    try:
        chunks = [message[i:i + 1900] for i in range(0, len(message), 1900)]
        for chunk in chunks: requests.post(WEBHOOK_URL, json={"content": chunk}, timeout=15)
    except Exception as e: print(f"Discord 전송 실패: {e}")

# ==========================================
# 2. 1단계: 깔때기 필터 (미국 우량주 600개 중 차트 후보 발굴)
# ==========================================
def get_candidate_tickers():
    try:
        # S&P500 + Nasdaq100 종목 가져오기
        sp500 = pd.read_html("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies")[0]['Symbol'].tolist()
        nasdaq100 = pd.read_html("https://en.wikipedia.org/wiki/Nasdaq-100")[4]['Ticker'].tolist()
        tickers = list(set(sp500 + nasdaq100))[:600]
        
        # 주가 데이터만 가볍게 다운로드 (속도 최적화)
        data = yf.download(tickers, period="30d", group_by='ticker', progress=False)
        candidates = []
        
        for t in tickers:
            try:
                df = data[t].dropna()
                if len(df) < 20: continue
                
                curr_close = df["Close"].iloc[-1]
                ma20 = df["Close"].rolling(20).mean().iloc[-1]
                # 20일선 근처거나 아래에 있는 종목들만 필터링 (바닥권 + 눌림목)
                if curr_close < ma20 * 1.1: 
                    candidates.append(t)
            except: continue
        return candidates
    except: return []

# ==========================================
# 3. 2단계: 옵션 정밀 분석 (세력 돋보기)
# ==========================================
def analyze_options(ticker):
    try:
        tk = yf.Ticker(ticker)
        if not tk.options: return None
        
        chain = tk.option_chain(tk.options[0])
        calls, puts = chain.calls.fillna(0), chain.puts.fillna(0)
        
        total_call_oi, total_put_oi = calls["openInterest"].sum(), puts["openInterest"].sum()
        if total_call_oi < 500: return None
        
        oi_pcr = total_put_oi / max(total_call_oi, 1)
        hist = tk.history(period="1mo")
        curr_close, ma20 = hist["Close"].iloc[-1], hist["Close"].rolling(20).mean().iloc[-1]
        pct = ((curr_close - hist["Close"].iloc[-2]) / hist["Close"].iloc[-2]) * 100
        
        # 타깃 분석
        otm_calls = calls[calls["strike"] > curr_close]
        if not otm_calls.empty:
            best_call = otm_calls.loc[otm_calls["openInterest"].idxmax()]
            call_conc = (best_call["openInterest"] / total_call_oi) * 100
        else: return None

        # [투트랙 조건]
        if oi_pcr < 0.5:
            # 눌림목 패턴
            if (curr_close >= ma20 * 0.98) and (-2.0 <= pct <= 2.0) and call_conc >= 20.0:
                return f"📈 **{ticker} [세력 눌림목 매집]** 🎯 목표가 ${best_call['strike']:.2f} (집중도 {call_conc:.1f}%)"
            # 바닥 패턴
            elif (curr_close < ma20) and (-5.0 <= pct <= 1.5) and call_conc >= 25.0:
                return f"🔥 **{ticker} [지하실 반격 매집]** 🎯 목표가 ${best_call['strike']:.2f} (집중도 {call_conc:.1f}%)"
        return None
    except: return None

# ==========================================
# 4. 메인 실행
# ==========================================
if __name__ == "__main__":
    print("스캔 시작...")
    candidates = get_candidate_tickers()
    msg = f"🚀 **미국 우량주 세력 엑기스 발굴 ({len(candidates)}개 후보 중)**\n"
    
    found = []
    for t in candidates[:100]: # 서버 부하 방지를 위해 상위 100개만 정밀 분석
        res = analyze_options(t)
        if res: found.append(res)
        time.sleep(0.3)
        
    if found:
        msg += "\n".join(found)
    else:
        msg += "현재 조건에 맞는 매집 종목 없음."
        
    send_discord(msg)
