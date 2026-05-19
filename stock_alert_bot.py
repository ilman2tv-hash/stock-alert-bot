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
MARKET_MODE = os.getenv("MARKET_MODE", "KR")

translator = Translator()

PERIOD = "1y"
INTERVAL = "1d"
KR_TOP_N = 80
US_TOP_N = 80
SIGNAL_LOOKBACK_DAYS = 2
ST_ATR_PERIOD = 10
ST_FACTOR = 3.0

def send_discord(message):
    if not WEBHOOK_URL or "http" not in WEBHOOK_URL:
        print("Webhook URL 오류")
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
        spy = close["SPY"].dropna()
        qqq = close["QQQ"].dropna()
        vix = close["^VIX"].dropna()
        spy_risk_on = spy.ewm(span=10).mean().iloc[-1] > spy.ewm(span=30).mean().iloc[-1]
        qqq_risk_on = qqq.ewm(span=10).mean().iloc[-1] > qqq.ewm(span=30).mean().iloc[-1]
        vix_val = float(vix.iloc[-1])
        score = int(spy_risk_on) + int(qqq_risk_on) + int(vix_val < 20)
        status = ["위험", "약세", "보통", "매우좋음"][score]
        return f"📊 시장상황: {status} | VIX: {vix_val:.2f}"
    except:
        return "📊 시장상황: 데이터 부족"

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
                try: t = translator.translate(t, dest="ko").text
                except: pass
            titles.append(t)
        return titles if titles else ["관련 뉴스 없음"]
    except:
        return ["뉴스 검색 오류"]

def get_high_conf_us_option_signal():
    try:
        data = yf.download(["SPY", "^VIX", "^PCCR"], period="5d", interval="1d", progress=False)
        if data.empty: return None
        close = data["Close"]
        curr_pccr = float(close["^PCCR"].dropna().iloc[-1])
        curr_vix = float(close["^VIX"].dropna().iloc[-1])
        curr_spy = float(close["SPY"].dropna().iloc[-1])
        prev_spy = float(close["SPY"].dropna().iloc[-2])
        if curr_pccr < 0.60 and curr_vix < 20 and curr_spy > prev_spy:
            return f"🔥 **[미국 옵션 상방 신호]** 기관 콜옵션 매수 집중! (PCCR: {curr_pccr:.2f})"
        elif curr_pccr > 1.10 and curr_vix > 25 and curr_spy < prev_spy:
            return f"🚨 **[미국 옵션 하방 주의]** 기관 풋옵션 대량 유입! (PCCR: {curr_pccr:.2f})"
        return None
    except:
        return None

def rma(series, length): return series.ewm(alpha=1/length, adjust=False).mean()
def crossover(a, b): return (a > b) & (a.shift(1) <= b.shift(1))
def crossunder(a, b): return (a < b) & (a.shift(1) >= b.shift(1))

def calculate_signals(df):
    df = df.copy()
    if isinstance(df.columns, pd.MultiIndex): df.columns = df.columns.get_level_values(0)
    high, low, close = df["High"], df["Low"], df["Close"]
    tr = pd.concat([high-low, (high-close.shift(1)).abs(), (low-close.shift(1)).abs()], axis=1).max(axis=1)
    atr = rma(tr, 14)
    up, down = high.diff(), -low.diff()
    plus_dm = np.where((up > down) & (up > 0), up, 0.0)
    minus_dm = np.where((down > up) & (down > 0), down, 0.0)
    df["diplus"] = 100 * rma(pd.Series(plus_dm, index=df.index), 14) / atr
    df["diminus"] = 100 * rma(pd.Series(minus_dm, index=df.index), 14) / atr
    df["ma20"] = df["Close"].rolling(20).mean()
    df["obv"] = np.where(df["Close"] > df["Close"].shift(1), df["Volume"], np.where(df["Close"] < df["Close"].shift(1), -df["Volume"], 0)).cumsum()
    df["obvUp"] = df["obv"] > df["obv"].shift(1)
    df["senkouA"] = (((df.High.rolling(9).max() + df.Low.rolling(9).min())/2 + (df.High.rolling(26).max() + df.Low.rolling(26).min())/2)/2).shift(26)
    df["senkouB"] = ((df.High.rolling(52).max() + df.Low.rolling(52).min())/2).shift(26)
    hl2 = (df["High"] + df["Low"]) / 2
    atr_st = rma(tr, ST_ATR_PERIOD)
    upper = hl2 + ST_FACTOR * atr_st
    lower = hl2 - ST_FACTOR * atr_st
    st = upper.copy()
    dir_st = pd.Series(1, index=df.index)
    for i in range(1, len(df)):
        if df["Close"].iloc[i] > st.iloc[i-1]: dir_st.iloc[i] = -1
        elif df["Close"].iloc[i] < st.iloc[i-1]: dir_st.iloc[i] = 1
        else: dir_st.iloc[i] = dir_st.iloc[i-1]
        st.iloc[i] = lower.iloc[i] if dir_st.iloc[i] == -1 else upper.iloc[i]
    df["stDirection"] = dir_st
    df["BUY"] = crossover(df["diplus"], df["diminus"]) & df["obvUp"] & (df["Close"] > df["ma20"]) & (df["Close"] > df[["senkouA", "senkouB"]].max(axis=1))
    df["ST_BUY"] = (df["stDirection"] < 0) & (df["stDirection"].shift(1) > 0)
    df["FULL_SELL"] = (df["stDirection"] > 0) & (df["stDirection"].shift(1) < 0)
    return df

def get_kr_tickers(top_n=80):
    date = (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")
    res = {}
    for m, s in [("KOSPI", ".KS"), ("KOSDAQ", ".KQ")]:
        try:
            df = stock.get_market_cap_by_ticker(date, market=m).sort_values("거래대금", ascending=False).head(top_n//2)
            for c in df.index: res[f"{c}{s}"] = stock.get_market_ticker_name(c)
        except: pass
    return res

def get_us_tickers(top_n=80):
    try:
        table = pd.read_html("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies")[0]
        syms = table["Symbol"].str.replace(".", "-", regex=False).tolist()[:top_n]
        return {s: s for s in syms}
    except: return {}

if __name__ == "__main__":
    print(f"=== {MARKET_MODE} 모드 실행 ===")
    m_status = get_market_status()

    if MARKET_MODE == "US_OPTION":
        sig = get_high_conf_us_option_signal()
        if sig:
            send_discord(f"🇺🇸 **미국 옵션 실시간 이상징후**\n━━━━━━━━━━━━━━━━━━\n{sig}\n{m_status}\n━━━━━━━━━━━━━━━━━━")
        else:
            # 옵션은 특이사항 없으면 조용히 종료 (스팸 방지)
            print("미국 옵션 특이사항 없음")

    else:
        # 국장/미장 종목 스캔은 예전처럼 무조건 보고
        target_tickers = {}
        if MARKET_MODE in ["KR", "ALL"]: target_tickers.update(get_kr_tickers(KR_TOP_N))
        if MARKET_MODE in ["US", "ALL"]: target_tickers.update(get_us_tickers(US_TOP_N))
        
        found_signals = []
        for t, name in target_tickers.items():
            try:
                df = yf.download(t, period=PERIOD, interval=INTERVAL, progress=False)
                if len(df) < 50: continue
                df = calculate_signals(df)
                last, prev = df.iloc[-1], df.iloc[-2]
                s_type = "ST BUY" if (last["stDirection"] < 0 and prev["stDirection"] > 0) else "BUY" if (last["BUY"]) else "FULL SELL" if (last["stDirection"] > 0 and prev["stDirection"] < 0) else None
                if s_type: found_signals.append({"t": t, "n": name, "s": s_type, "p": last["Close"]})
            except: continue
            
        if found_signals:
            msg = f"🚨 [{MARKET_MODE}] 스캔 결과\n{m_status}\n"
            for s in found_signals:
                news = "\n".join([f"• {n}" for n in get_news_titles(s["n"], s["t"])])
                msg += f"\n[{s['s']}] {s['n']} ({s['t']})\n💰 현재가: {float(s['p']):.2f}\n{news}\n"
            send_discord(msg)
        else:
            # 주식 스캔은 특이사항 없어도 보고
            send_discord(f"✅ [{MARKET_MODE}] 시장 스캔 완료\n{m_status}\n현재 분석 대상 중 특이 신호 종목 없음")

    print("=== 실행 완료 ===")
