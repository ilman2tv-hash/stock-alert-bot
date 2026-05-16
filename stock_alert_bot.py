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

WEBHOOK_URL = os.getenv("WEBHOOK_URL")
MARKET_MODE = os.getenv("MARKET_MODE", "ALL")

translator = Translator()

PERIOD = "1y"
INTERVAL = "1d"

KR_TOP_N = 80
US_TOP_N = 80

# 최근 2일 신호만 체크
SIGNAL_LOOKBACK_DAYS = 2

FAKE_BUY_BLOCK_PCT = 5.0

ST_ATR_PERIOD = 10
ST_FACTOR = 3.0

CRYPTO_TICKERS = {
    "BTC-USD": "비트코인",
    "ETH-USD": "이더리움",
    "SOL-USD": "솔라나",
    "BNB-USD": "BNB",
    "XRP-USD": "리플",
    "ADA-USD": "에이다",
    "AVAX-USD": "아발란체",
    "LINK-USD": "체인링크",
    "DOGE-USD": "도지코인",
    "SUI-USD": "수이",
}


def send_discord(message):
    print("=== Discord 전송 시도 ===")

    if not WEBHOOK_URL or "http" not in WEBHOOK_URL:
        print("Webhook URL이 설정되지 않았습니다.")
        return

    try:
        chunks = [message[i:i + 1900] for i in range(0, len(message), 1900)]

        for chunk in chunks:
            res = requests.post(
                WEBHOOK_URL,
                json={"content": chunk},
                timeout=15
            )

            print("Discord 응답:", res.status_code)

    except Exception as e:
        print("Discord 전송 오류:", e)


def get_market_status():
    try:
        data = yf.download(
            ["SPY", "QQQ", "^VIX"],
            period="3mo",
            interval="1d",
            progress=False,
            auto_adjust=False
        )

        if data.empty:
            return "📊 시장상황: 조회 실패"

        close = data["Close"]

        spy = close["SPY"].dropna()
        qqq = close["QQQ"].dropna()
        vix = close["^VIX"].dropna()

        spy_risk_on = spy.ewm(span=10, adjust=False).mean().iloc[-1] > spy.ewm(span=30, adjust=False).mean().iloc[-1]
        qqq_risk_on = qqq.ewm(span=10, adjust=False).mean().iloc[-1] > qqq.ewm(span=30, adjust=False).mean().iloc[-1]

        vix_value = float(vix.iloc[-1])
        vix_safe = vix_value < 20

        score = int(spy_risk_on) + int(qqq_risk_on) + int(vix_safe)

        market_status = (
            "매우좋음" if score == 3 else
            "보통" if score == 2 else
            "약세" if score == 1 else
            "위험"
        )

        return (
            f"📊 시장상황: {market_status}\n"
            f"🇺🇸 미국시장: {'상승' if spy_risk_on else '약세'}\n"
            f"💻 기술주: {'상승' if qqq_risk_on else '약세'}\n"
            f"😱 VIX: {vix_value:.2f}"
        )

    except Exception as e:
        print("시장상황 조회 오류:", e)
        return "📊 시장상황: 조회 실패"


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
                try:
                    title = translator.translate(title, dest="ko").text
                except:
                    pass

            if title and title not in seen:
                news_titles.append(title)
                seen.add(title)

            if len(news_titles) >= 3:
                break

        return news_titles if news_titles else ["관련 뉴스 없음"]

    except Exception as e:
        print("뉴스 오류:", e)
        return ["뉴스 검색 오류"]


def get_recent_kr_date():
    for i in range(10):
        target_date = (datetime.now() - timedelta(days=i)).strftime("%Y%m%d")

        try:
            df = stock.get_market_cap_by_ticker(target_date, market="KOSPI")

            if not df.empty:
                return target_date

        except:
            pass

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

        except Exception as e:
            print(f"{market} 오류:", e)

    return result


def get_us_sp500_top_trading_value(top_n=80):
    try:
        url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"

        table = pd.read_html(url)[0]

        symbols = table["Symbol"].str.replace(".", "-", regex=False).tolist()

        candidates = {}

        for symbol in symbols[:150]:
            try:
                df = yf.download(
                    symbol,
                    period="5d",
                    interval="1d",
                    progress=False,
                    auto_adjust=False
                )

                if isinstance(df.columns, pd.MultiIndex):
                    df.columns = df.columns.get_level_values(0)

                if df.empty:
                    continue

                last = df.iloc[-1]

                close = float(last["Close"])
                volume = float(last["Volume"])

                candidates[symbol] = close * volume

            except:
                continue

        sorted_items = sorted(
            candidates.items(),
            key=lambda x: x[1],
            reverse=True
        )[:top_n]

        return {symbol: symbol for symbol, _ in sorted_items}

    except Exception as e:
        print("미장 오류:", e)
        return {}


def build_tickers_by_mode():
    if MARKET_MODE == "KR":
        return get_kr_top_trading_value(KR_TOP_N)

    elif MARKET_MODE == "US":
        return get_us_sp500_top_trading_value(US_TOP_N)

    elif MARKET_MODE == "CRYPTO":
        return CRYPTO_TICKERS

    else:
        all_t = get_kr_top_trading_value(KR_TOP_N)
        all_t.update(get_us_sp500_top_trading_value(US_TOP_N))
        all_t.update(CRYPTO_TICKERS)

        return all_t


def rma(series, length):
    return series.ewm(alpha=1 / length, adjust=False).mean()


def crossover(a, b):
    return (a > b) & (a.shift(1) <= b.shift(1))


def crossunder(a, b):
    return (a < b) & (a.shift(1) >= b.shift(1))


def calculate_dmi(df, length=14, smoothing=14):
    high = df["High"]
    low = df["Low"]
    close = df["Close"]

    up = high.diff()
    down = -low.diff()

    plus_dm = np.where((up > down) & (up > 0), up, 0.0)
    minus_dm = np.where((down > up) & (down > 0), down, 0.0)

    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()

    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    atr = rma(tr, length)

    plus_di = 100 * rma(pd.Series(plus_dm, index=df.index), length) / atr
    minus_di = 100 * rma(pd.Series(minus_dm, index=df.index), length) / atr

    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di)

    adx = rma(dx, smoothing)

    return plus_di, minus_di, adx


def calculate_supertrend(df, atr_period=10, factor=3.0):
    high = df["High"]
    low = df["Low"]
    close = df["Close"]

    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()

    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    atr = rma(tr, atr_period)

    hl2 = (high + low) / 2

    upper_basic = hl2 + factor * atr
    lower_basic = hl2 - factor * atr

    upper_band = upper_basic.copy()
    lower_band = lower_basic.copy()

    direction = pd.Series(index=df.index, dtype=float)
    supertrend = pd.Series(index=df.index, dtype=float)

    for i in range(len(df)):

        if i == 0:
            direction.iloc[i] = 1
            supertrend.iloc[i] = upper_band.iloc[i]
            continue

        prev_close = close.iloc[i - 1]

        if upper_basic.iloc[i] < upper_band.iloc[i - 1] or prev_close > upper_band.iloc[i - 1]:
            upper_band.iloc[i] = upper_basic.iloc[i]
        else:
            upper_band.iloc[i] = upper_band.iloc[i - 1]

        if lower_basic.iloc[i] > lower_band.iloc[i - 1] or prev_close < lower_band.iloc[i - 1]:
            lower_band.iloc[i] = lower_basic.iloc[i]
        else:
            lower_band.iloc[i] = lower_band.iloc[i - 1]

        if direction.iloc[i - 1] == 1:
            direction.iloc[i] = -1 if close.iloc[i] > upper_band.iloc[i] else 1
        else:
            direction.iloc[i] = 1 if close.iloc[i] < lower_band.iloc[i] else -1

        supertrend.iloc[i] = (
            lower_band.iloc[i]
            if direction.iloc[i] == -1
            else upper_band.iloc[i]
        )

    return supertrend, direction


def calculate_signals(df):
    df = df.copy()

    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    df["diplus"], df["diminus"], df["adx"] = calculate_dmi(df)

    df["ma20"] = df["Close"].rolling(20).mean()

    df["obv"] = np.where(
        df["Close"] > df["Close"].shift(1),
        df["Volume"],
        np.where(
            df["Close"] < df["Close"].shift(1),
            -df["Volume"],
            0
        )
    ).cumsum()

    df["obvUp"] = df["obv"] > df["obv"].shift(1)

    df["tenkan"] = (
        df["High"].rolling(9).max() +
        df["Low"].rolling(9).min()
    ) / 2

    df["kijun"] = (
        df["High"].rolling(26).max() +
        df["Low"].rolling(26).min()
    ) / 2

    df["senkouA"] = (df["tenkan"] + df["kijun"]) / 2

    df["senkouB"] = (
        df["High"].rolling(52).max() +
        df["Low"].rolling(52).min()
    ) / 2

    df["cloudTop"] = df[["senkouA", "senkouB"]].max(axis=1)

    df["aboveCloud"] = df["Close"] > df["cloudTop"]

    df["supertrend"], df["stDirection"] = calculate_supertrend(
        df,
        ST_ATR_PERIOD,
        ST_FACTOR
    )

    df["superTrendBuy"] = (
        (df["stDirection"] < 0) &
        (df["stDirection"].shift(1) > 0)
    )

    df["superTrendFullSell"] = (
        (df["stDirection"] > 0) &
        (df["stDirection"].shift(1) < 0)
    )

    df["BUY"] = (
        crossover(df["diplus"], df["diminus"]) &
        df["obvUp"] &
        (df["Close"] > df["ma20"]) &
        df["aboveCloud"]
    )

    df["ST_BUY"] = df["superTrendBuy"]

    df["SELL_1_3"] = (
        crossunder(df["Close"], df["ma20"]) |
        crossunder(df["Close"], df["cloudTop"])
    )

    df["SELL_1_2"] = (
        crossunder(df["diplus"], df["diminus"]) |
        crossunder(df["Close"], df["kijun"])
    )

    df["FULL_SELL"] = df["superTrendFullSell"]

    return df


print("=== 실행 시작 ===")

market_status_text = get_market_status()

TICKERS = build_tickers_by_mode()

print("총 종목 수:", len(TICKERS))

all_latest_signals = []

for ticker, name in TICKERS.items():

    try:
        print("스캔:", ticker)

        df = yf.download(
            ticker,
            period=PERIOD,
            interval=INTERVAL,
            progress=False,
            auto_adjust=False
        )

        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        if df.empty or len(df) < 80:
            continue

        df = calculate_signals(df)

        # 최근 2일만 체크
        last_rows = df.tail(SIGNAL_LOOKBACK_DAYS)

        for idx, row in last_rows.iterrows():

            signal = None

            if bool(row["ST_BUY"]):
                signal = "ST BUY"

            elif bool(row["BUY"]):
                signal = "BUY"

            elif bool(row["SELL_1_3"]):
                signal = "1/3 SELL"

            elif bool(row["SELL_1_2"]):
                signal = "1/2 SELL"

            elif bool(row["FULL_SELL"]):
                signal = "FULL SELL"

            if signal:
                all_latest_signals.append({
                    "ticker": ticker,
                    "name": name,
                    "date": idx,
                    "close": row["Close"],
                    "signal": signal
                })

                print(signal, ticker)

    except Exception as e:
        print("오류:", ticker, e)


# 최신 날짜순 정렬
all_latest_signals = sorted(
    all_latest_signals,
    key=lambda x: x["date"],
    reverse=True
)

if all_latest_signals:

    msg = (
        f"🚨 [{MARKET_MODE}] 자동 스캔 결과\n\n"
        f"{market_status_text}\n"
    )

    for s in all_latest_signals:

        news = "\n".join([
            f"• {t}"
            for t in get_news_titles(s["name"], s["ticker"])
        ])

        msg += (
            f"\n[{s['signal']}] "
            f"{s['name']} ({s['ticker']})\n"
            f"📅 날짜: {s['date'].strftime('%Y-%m-%d')}\n"
            f"💰 가격: {float(s['close']):.2f}\n"
            f"📰 뉴스:\n{news}\n"
        )

    send_discord(msg)

else:

    send_discord(
        f"✅ [{MARKET_MODE}] 스캔 완료\n"
        f"최근 2일 특이 신호 없음\n\n"
        f"{market_status_text}"
    )

print("=== 실행 종료 ===")
