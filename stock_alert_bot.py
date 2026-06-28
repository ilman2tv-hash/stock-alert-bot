import os
import time
import requests
import pandas as pd
import numpy as np
import yfinance as yf
from pykrx import stock

WEBHOOK_URL = os.getenv("WEBHOOK_URL")
MARKET_MODE = os.getenv("MARKET_MODE", "KR")


# =========================
# Discord
# =========================
def send_discord(msg):
    if not WEBHOOK_URL:
        return

    for i in range(0, len(msg), 1900):
        try:
            requests.post(WEBHOOK_URL, json={"content": msg[i:i+1900]}, timeout=10)
        except:
            pass


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
# US Universe (S&P500)
# =========================
def get_us():
    try:
        df = pd.read_html(
            "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
        )[0]
        return df["Symbol"].tolist()
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
# TOP 300 유동성 필터
# =========================
def get_top_universe(tickers):

    try:
        data = yf.download(
            tickers,
            period="30d",
            group_by="ticker",
            threads=True,
            progress=False
        )

        scores = []

        for t in tickers:

            try:
                if t not in data.columns.levels[0]:
                    continue

                df = data[t].dropna()

                if len(df) < 25:
                    continue

                vol = df["Volume"]

                avg_vol = vol.rolling(20).mean().iloc[-1]
                curr_vol = vol.iloc[-1]

                if avg_vol == 0 or np.isnan(avg_vol):
                    continue

                ratio = curr_vol / avg_vol

                price = df["Close"].iloc[-1]
                ma20 = df["Close"].rolling(20).mean().iloc[-1]

                score = 0
                score += ratio * 50

                if price > ma20:
                    score += 30

                scores.append((t, score))

            except:
                continue

        scores.sort(key=lambda x: x[1], reverse=True)

        return [t for t, _ in scores[:300]]

    except:
        return []


# =========================
# 매수 신호
# =========================
def get_signal(df):

    ma5 = df["Close"].rolling(5).mean()
    ma20 = df["Close"].rolling(20).mean()

    price = df["Close"].iloc[-1]

    if price > ma20.iloc[-1] and ma5.iloc[-1] > ma20.iloc[-1]:
        return "MAIN_BUY"

    if price > ma20.iloc[-1]:
        return "ST_BUY"

    return None


# =========================
# 옵션 세력 분석
# =========================
def analyze_options(ticker):

    try:
        tk = yf.Ticker(ticker)

        if not tk.options:
            return None

        chain = tk.option_chain(tk.options[0])

        calls = chain.calls
        puts = chain.puts

        call_oi = calls["openInterest"].sum()
        put_oi = puts["openInterest"].sum()

        if call_oi == 0:
            return None

        pcr = put_oi / call_oi

        price = tk.history(period="1d")["Close"].iloc[-1]

        otm_calls = calls[calls["strike"] > price]

        if otm_calls.empty:
            return None

        top = otm_calls.loc[otm_calls["openInterest"].idxmax()]

        oi_ratio = top["openInterest"] / call_oi * 100

        if pcr < 0.7 and oi_ratio > 20:
            return {
                "type": "ACCUMULATION",
                "pcr": round(pcr, 2),
                "strike": round(top["strike"], 2),
                "oi_ratio": round(oi_ratio, 1)
            }

        if pcr < 0.5:
            return {
                "type": "STRONG_ACCUMULATION",
                "pcr": round(pcr, 2),
                "strike": round(top["strike"], 2),
                "oi_ratio": round(oi_ratio, 1)
            }

        return None

    except:
        return None


# =========================
# 종목 분석
# =========================
def analyze(ticker):

    df = yf.download(ticker, period="6mo", interval="1d", progress=False)

    if df is None or len(df) < 60:
        return None

    signal = get_signal(df)

    if not signal:
        return None

    price = df["Close"].iloc[-1]

    opt = analyze_options(ticker)

    msg = f"""
📊 {ticker}
🟢 신호: {signal}
💰 현재가: {price:.2f}
"""

    if opt:
        msg += f"""
🔥 옵션 세력
- 타입: {opt['type']}
- PCR: {opt['pcr']}
- 행사가: {opt['strike']}
- 콜 집중도: {opt['oi_ratio']}%
"""

    return msg


# =========================
# 실행
# =========================
def run():

    universe = get_universe()

    if not universe:
        send_discord("❌ Universe 없음")
        return

    top = get_top_universe(universe)

    results = []

    for t in top[:50]:

        res = analyze(t)

        if res:
            results.append(res)

        time.sleep(0.2)

    if results:
        msg = f"🚀 STOCK ALERT ({MARKET_MODE})\n\n" + "\n\n----------------\n\n".join(results)
    else:
        msg = f"📉 ({MARKET_MODE}) 신호 없음"

    send_discord(msg)


if __name__ == "__main__":
    run()
