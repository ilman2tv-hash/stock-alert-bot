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
MARKET_MODE = os.getenv("MARKET_MODE", "ALL")

translator = Translator()

PERIOD = "1y"
INTERVAL = "1d"
KR_TOP_N = 80
US_TOP_N = 80
SIGNAL_LOOKBACK_DAYS = 2
FAKE_BUY_BLOCK_PCT = 5.0
ST_ATR_PERIOD = 10
ST_FACTOR = 3.0

# --- 유틸리티 함수 ---
def send_discord(message):
    if not WEBHOOK_URL or "http" not in WEBHOOK_URL:
        print("Webhook URL 없음")
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
        vix_status = "안정" if vix_val < 20 else "경계" if vix_val < 30 else "위험"
        return f"📊 시장상황: {status}\n🇺🇸 미국시장: {'상승' if spy_risk_on else '약세'}\n💻 기술주: {'상승' if qqq_risk_on else '약세'}\n😱 공포지수: {vix_val:.2f} ({vix_status})"
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

# --- 미국 옵션 고신뢰도 수급 분석 ---
def get_high_conf_us_option_signal():
    try:
        # SPY, VIX, Put/Call Ratio 데이터 수집
        data = yf.download(["SPY", "^VIX", "^PCCR"], period="5d", interval="1d", progress=False)
        if data.empty: return None
        close = data["Close"]
        curr_pccr = float(close["^PCCR"].dropna().iloc[-1])
        curr_vix = float(close["^VIX"].dropna().iloc[-1])
        curr_spy = float(close["SPY"].dropna().iloc[-1])
        prev_spy = float(close["SPY"].dropna().iloc[-2])

        # 상방: 콜옵션 비중 높음 + 지수 상승 + 변동성 안정
        if curr_pccr < 0.60 and curr_vix < 20 and curr_spy > prev_spy:
            return f"🔥 **[미국 옵션 상방 신호]** 기관 콜옵션 매수 집중! (PCCR: {curr_pccr:.2f})"
        # 하방: 풋옵션 비중 높음 + 지수 하락 + 변동성 폭등
        elif curr_pccr > 1.10 and curr_vix > 25 and curr_spy < prev_spy:
            return f"🚨 **[미국 옵션 하방 주의]** 기관 풋옵션 대량 유입! (PCCR: {curr_pccr:.2f})"
        return None
    except:
        return None

# --- 지수 및 종목 분석 (기존 로직 유지) ---
def rma(series, length): return series.ewm(alpha=1 / length, adjust=False).mean()
def crossover(a, b): return (a > b) & (a.shift(1) <= b.shift(1))
def crossunder(a, b): return (a < b) & (a.shift(1) >= b.shift(1))

def calculate_dmi(df, length=14, smoothing=14):
    high, low, close = df["High"], df["Low"], df["Close"]
    up, down = high.diff(), -low.diff()
    plus_dm = np.where((up > down) & (up > 0), up, 0.0)
    minus_dm = np.where((down > up) & (down > 0), down, 0.0)
    tr = pd.concat([high - low, (high - close.shift(1)).abs(), (low - close.shift(1)).abs()], axis=1).max(axis=1)
    atr = rma(tr, length)
    plus_di = 100 * rma(pd.Series(plus_dm, index=df.index), length) / atr
    minus_di = 100 * rma(pd.Series(minus_dm, index=df.index), length) / atr
    adx = rma((plus_di - minus_di).abs() / (plus_di + minus_di) * 100, smoothing)
    return plus_di, minus_di, adx

def calculate_supertrend(df, atr_period=10, factor=3.0):
    high, low, close = df["High"], df["Low"], df["Close"]
    tr = pd.concat([high - low, (high - close.shift(1)).abs(), (low - close.shift(1)).abs()], axis=1).max(axis=1)
    atr = rma(tr, atr_period)
    hl2 = (high + low) / 2
    upper_band = hl2 + factor * atr
    lower_band = hl2 - factor * atr
    # 간략화된 슈퍼트렌드 로직 (사용자님 원본 반영)
    direction = pd.Series(1, index=df.index)
    st = upper_band.copy()
    for i in range(1, len(df)):
        if close.iloc[i] > st.iloc[i-1]: direction.iloc[i] = -1
        elif close.iloc[i] < st.iloc[i-1]: direction.iloc[i] = 1
        else: direction.iloc[i] = direction.iloc[i-1]
        st.iloc[i] = lower_band.iloc[i] if direction.iloc[i] == -1 else upper_band.iloc[i]
    return st, direction

def calculate_signals(df):
    df = df.copy()
    if isinstance(df.columns, pd.MultiIndex): df.columns = df.columns.get_level_values(0)
    df["diplus"], df["diminus"], df["adx"] = calculate_dmi(df)
    df["ma20"], df["ma5"] = df["Close"].rolling(20).mean(), df["Close"].rolling(5).mean()
    df["obv"] = np.where(df["Close"] > df["Close"].shift(1), df["Volume"], np.where(df["Close"] < df["Close"].shift(1), -df["Volume"], 0)).cumsum()
    df["obvUp"] = df["obv"] > df["obv"].shift(1)
    df["tenkan"] = (df.High.rolling(9).max() + df.Low.rolling(9).min()) / 2
    df["kijun"] = (df.High.rolling(26).max() + df.Low.rolling(26).min()) / 2
    df["senkouA"] = ((df.tenkan + df.kijun) / 2).shift(26)
    df["senkouB"] = ((df.High.rolling(52).max() + df.Low.rolling(52).min()) / 2).shift(26)
    df["aboveCloud"] = df["Close"] > df[["senkouA", "senkouB"]].max(axis=1)
    df["supertrend"], df["stDirection"] = calculate_supertrend(df, ST_ATR_PERIOD, ST_FACTOR)
    
    df["BUY"] = crossover(df["diplus"], df["diminus"]) & df["obvUp"] & (df["Close"] > df["ma20"]) & df["aboveCloud"]
    df["ST_BUY"] = (df["stDirection"] < 0) & (df["stDirection"].shift(1) > 0)
    df["SELL_1_3"] = crossunder(df["Close"], df["ma20"])
    df["SELL_1_2"] = crossunder(df["diplus"], df["diminus"])
    df["FULL_SELL"] = (df["stDirection"] > 0) & (df["stDirection"].shift(1) < 0)
    return df

# --- 시장 종목 구성 (코인 제거) ---
def get_kr_top_trading_value(top_n=80):
    date_str = (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")
    result = {}
    for m, s in [("KOSPI", ".KS"), ("KOSDAQ", ".KQ")]:
        try:
            df = stock.get_market_cap_by_ticker(date_str, market=m).sort_values("거래대금", ascending=False).head(top_n // 2)
            for code in df.index: result[f"{code}{s}"] = stock.get_market_ticker_name(code)
        except: pass
    return result

def get_us_sp500_top_trading_value(top_n=80):
    try:
        table = pd.read_html("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies")[0]
        symbols = table["Symbol"].str.replace(".", "-", regex=False).tolist()[:top_n]
        return {s: s for s in symbols}
    except: return {}

def build_tickers_by_mode():
    if MARKET_MODE == "KR": return get_kr_top_trading_value(KR_TOP_N)
    if MARKET_MODE == "US": return get_us_sp500_top_trading_value(US_TOP_N)
    if MARKET_MODE == "US_OPTION": return {} # 옵션 전용 모드
    all_t = get_kr_top_trading_value(KR_TOP_N)
    all_t.update(get_us_sp500_top_trading_value(US_TOP_N))
    return all_t

# --- 메인 실행 로직 ---
if __name__ == "__main__":
    print(f"=== 실행 시작 (모드: {MARKET_MODE}) ===")
    market_status_text = get_market_status()

    # 1. 미국 옵션 모드 실행
    if MARKET_MODE == "US_OPTION":
        option_signal = get_high_conf_us_option_signal()
        if option_signal:
            msg = f"🇺🇸 **미국 옵션 실시간 이상징후**\n━━━━━━━━━━━━━━━━━━\n{option_signal}\n{market_status_text}\n━━━━━━━━━━━━━━━━━━"
            send_discord(msg)
        else:
            print("미국 옵션 수급 특이사항 없음")

    # 2. 일반 주식 스캔 모드 실행
    else:
        TICKERS = build_tickers_by_mode()
        all_signals = []
        for ticker, name in TICKERS.items():
            try:
                df = yf.download(ticker, period=PERIOD, interval=INTERVAL, progress=False)
                if df.empty or len(df) < 80: continue
                df = calculate_signals(df)
                last_rows = df.tail(SIGNAL_LOOKBACK_DAYS)
                for idx, row in last_rows.iterrows():
                    sig = "ST BUY" if row["ST_BUY"] else "BUY" if row["BUY"] else "1/3 SELL" if row["SELL_1_3"] else "1/2 SELL" if row["SELL_1_2"] else "FULL SELL" if row["FULL_SELL"] else None
                    if sig: all_signals.append({"ticker": ticker, "name": name, "date": idx, "close": row["Close"], "signal": sig})
            except: continue

        if all_signals:
            msg = f"🚨 [{MARKET_MODE}] 시장 스캔 결과\n{market_status_text}\n"
            for s in sorted(all_signals, key=lambda x: x["date"], reverse=True):
                news = "\n".join([f"• {t}" for t in get_news_titles(s["name"], s["ticker"])])
                msg += f"\n[{s['signal']}] {s['name']} ({s['ticker']})\n💰 가격: {float(s['close']):.2f}\n뉴스:\n{news}\n"
            send_discord(msg)
        else:
            send_discord(f"✅ [{MARKET_MODE}] 스캔 완료: 특이 신호 없음\n{market_status_text}")

    print("=== 실행 종료 ===")
