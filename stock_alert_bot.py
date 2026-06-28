import yfinance as yf
import pandas as pd
import numpy as np
import requests
import os
import time

WEBHOOK_URL = os.getenv("WEBHOOK_URL")


# =========================
# 1. 디스코드 전송
# =========================
def send_discord(message):
    if not WEBHOOK_URL:
        return
    try:
        chunks = [message[i:i + 1900] for i in range(0, len(message), 1900)]
        for c in chunks:
            requests.post(WEBHOOK_URL, json={"content": c}, timeout=15)
    except Exception as e:
        print("Discord error:", e)


# =========================
# 2. TradingView 신호 엔진 (네 기존 구조 유지)
# =========================
def get_tv_signal(df):

    close = df["Close"]
    volume = df["Volume"]

    ma5 = close.rolling(5).mean()
    ma20 = close.rolling(20).mean()

    obv = (close.diff().fillna(0) * volume).cumsum()

    i = -2

    if ma5.iloc[i] > ma20.iloc[i] and close.iloc[i] > ma20.iloc[i] and obv.iloc[i] > obv.iloc[i-1]:
        return "MAIN_BUY"

    if ma5.iloc[i] > ma20.iloc[i]:
        return "ST_BUY"

    if close.iloc[i] < ma20.iloc[i]:
        return "SELL"

    return None


# =========================
# 3. 차트 필터 (S&P + NASDAQ 후보군)
# =========================
def get_candidate_tickers():

    try:
        sp500 = pd.read_html("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies")[0]['Symbol'].tolist()
        nasdaq100 = pd.read_html("https://en.wikipedia.org/wiki/Nasdaq-100")[4]['Ticker'].tolist()

        tickers = list(set(sp500 + nasdaq100))[:300]

        data = yf.download(tickers, period="30d", group_by='ticker', progress=False)

        candidates = []

        for t in tickers:
            try:
                df = data[t].dropna()
                if len(df) < 20:
                    continue

                curr = df["Close"].iloc[-1]
                ma20 = df["Close"].rolling(20).mean().iloc[-1]

                if curr < ma20 * 1.15:
                    candidates.append(t)

            except:
                continue

        return candidates

    except:
        return []


# =========================
# 4. 옵션 세력 분석 (핵심)
# =========================
def analyze_options(ticker):

    try:
        tk = yf.Ticker(ticker)

        if not tk.options:
            return None

        chain = tk.option_chain(tk.options[0])
        calls = chain.calls.fillna(0)
        puts = chain.puts.fillna(0)

        call_oi = calls["openInterest"].sum()
        put_oi = puts["openInterest"].sum()

        if call_oi == 0:
            return None

        pcr = put_oi / call_oi

        price = tk.history(period="1d")["Close"].iloc[-1]

        otm_calls = calls[calls["strike"] > price]

        if otm_calls.empty:
            return None

        best = otm_calls.loc[otm_calls["openInterest"].idxmax()]
        concentration = (best["openInterest"] / call_oi) * 100

        if pcr < 0.5 and concentration >= 20:
            return f"📈 [{ticker}] 눌림목 매집\n🎯 목표가: {best['strike']:.2f} | 집중도 {concentration:.1f}% | PCR {pcr:.2f}"

        if pcr < 0.4 and concentration >= 25:
            return f"🔥 [{ticker}] 강한 세력 매집\n🎯 목표가: {best['strike']:.2f} | 집중도 {concentration:.1f}% | PCR {pcr:.2f}"

        return None

    except:
        return None


# =========================
# 5. 개별 종목 분석 (TV + 옵션 병합)
# =========================
def analyze_stock(ticker):

    df = yf.download(ticker, period="6mo", interval="1d", progress=False)

    if df is None or len(df) < 200:
        return None

    signal = get_tv_signal(df)

    option_signal = analyze_options(ticker)

    price = df["Close"].iloc[-2]

    if not signal and not option_signal:
        return None

    msg = f"📊 [{ticker}]\n현재가: {price}\n"

    if signal:
        msg += f"\n🔔 차트 신호: {signal}"

    if option_signal:
        msg += f"\n\n💣 옵션 세력:\n{option_signal}"

    return msg


# =========================
# 6. 실행
# =========================
def run():

    candidates = get_candidate_tickers()

    results = []

    for t in candidates[:50]:
        res = analyze_stock(t)
        if res:
            results.append(res)

        time.sleep(0.3)

    if results:
        msg = "🚀 세력 + 차트 통합 분석\n\n\n" + "\n\n-----------------\n\n".join(results)
    else:
        msg = "현재 강한 세력 + 차트 신호 없음"

    send_discord(msg)


if __name__ == "__main__":
    run()
