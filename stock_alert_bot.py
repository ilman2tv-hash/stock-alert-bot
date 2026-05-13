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

WEBHOOK_URL = os.getenv("WEBHOOK_URL", "https://discord.com/api/webhooks/1503073387428446519/RNFbVwgOreGt4Hc708en5oh81_lEfG78YHb_PrUhgUGcin6CBu9Oslf-xIziv34ON1Ky")
MARKET_MODE = os.getenv("MARKET_MODE", "ALL")

translator = Translator()

PERIOD = "1y"
INTERVAL = "1d"
KR_TOP_N = 80
US_TOP_N = 80
SIGNAL_LOOKBACK_DAYS = 3
FAKE_BUY_BLOCK_PCT = 5.0

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
    print("WEBHOOK_URL 있음:", bool(WEBHOOK_URL))
    print("메시지 길이:", len(message))

    if not WEBHOOK_URL or "http" not in WEBHOOK_URL:
        print("Webhook URL이 설정되지 않았습니다.")
        return

    try:
        chunks = [message[i:i + 1900] for i in range(0, len(message), 1900)]

        for idx, chunk in enumerate(chunks, start=1):
            res = requests.post(
                WEBHOOK_URL,
                json={"content": chunk},
                timeout=15
            )

            print(f"Discord 전송 {idx}/{len(chunks)} 응답코드:", res.status_code)
            print("Discord 응답내용:", res.text)

            if res.status_code not in [200, 204]:
                print("Discord 전송 실패")
                return

        print("Discord 전송 완료")

    except Exception as e:
        print("Discord 전송 오류:", e)


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
                except Exception as e:
                    print(f"번역 오류: {e}")

            if title and title not in seen:
                news_titles.append(title)
                seen.add(title)

            if len(news_titles) >= 3:
                break

        return news_titles if news_titles else ["관련 뉴스 없음"]

    except Exception as e:
        print(f"뉴스 검색 오류: {e}")
        return ["뉴스 검색 오류"]


def get_recent_kr_date():
    for i in range(10):
        target_date = (datetime.now() - timedelta(days=i)).strftime("%Y%m%d")
        try:
            df = stock.get_market_cap_by_ticker(target_date, market="KOSPI")
            if not df.empty:
                return target_date
        except Exception as e:
            print(f"국장 날짜 확인 오류 {target_date}: {e}")

    return datetime.now().strftime("%Y%m%d")


def get_kr_top_trading_value(top_n=80):
    date_str = get_recent_kr_date()
    print("국장 기준일:", date_str)

    result = {}

    for market, suffix in [("KOSPI", ".KS"), ("KOSDAQ", ".KQ")]:
        try:
            df = stock.get_market_cap_by_ticker(date_str, market=market)
            df = df.sort_values("거래대금", ascending=False).head(top_n // 2)

            for code in df.index:
                result[f"{code}{suffix}"] = stock.get_market_ticker_name(code)

            print(f"{market} 종목 수:", len(df))

        except Exception as e:
            print(f"{market} 종목 조회 오류:", e)

    return result


def get_us_sp500_top_trading_value(top_n=80):
    try:
        url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
        table = pd.read_html(url)[0]
        symbols = table["Symbol"].str.replace(".", "-", regex=False).tolist()

        candidates = {}

        for symbol in symbols[:150]:
            try:
                df = yf.download(symbol, period="5d", interval="1d", progress=False, auto_adjust=False)

                if isinstance(df.columns, pd.MultiIndex):
                    df.columns = df.columns.get_level_values(0)

                if df.empty:
                    continue

                last = df.iloc[-1]
                close = float(last["Close"])
                volume = float(last["Volume"])
                candidates[symbol] = close * volume

            except Exception as e:
                print(f"미장 거래대금 조회 오류 {symbol}: {e}")
                continue

        sorted_items = sorted(candidates.items(), key=lambda x: x[1], reverse=True)[:top_n]
        print("미장 후보 종목 수:", len(sorted_items))

        return {symbol: symbol for symbol, _ in sorted_items}

    except Exception as e:
        print("미장 S&P500 조회 오류:", e)
        return {}


def build_tickers_by_mode():
    print("MARKET_MODE:", MARKET_MODE)

    if MARKET_MODE == "KR":
        print("💡 모드: 국장 전용 스캔")
        return get_kr_top_trading_value(KR_TOP_N)

    elif MARKET_MODE == "US":
        print("💡 모드: 미장 전용 스캔")
        return get_us_sp500_top_trading_value(US_TOP_N)

    elif MARKET_MODE == "CRYPTO":
        print("💡 모드: 코인 전용 스캔")
        return CRYPTO_TICKERS

    else:
        print("💡 모드: 전체 스캔")
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


def calculate_signals(df):
    df = df.copy()

    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    df["diplus"], df["diminus"], df["adx"] = calculate_dmi(df, 14, 14)

    df["ma20"] = df["Close"].rolling(20).mean()
    df["ma5"] = df["Close"].rolling(5).mean()

    df["obv"] = np.where(
        df["Close"] > df["Close"].shift(1),
        df["Volume"],
        np.where(df["Close"] < df["Close"].shift(1), -df["Volume"], 0)
    ).cumsum()

    df["obvUp"] = df["obv"] > df["obv"].shift(1)

    df["tenkan"] = (df["High"].rolling(9).max() + df["Low"].rolling(9).min()) / 2
    df["kijun"] = (df["High"].rolling(26).max() + df["Low"].rolling(26).min()) / 2
    df["senkouA"] = (df["tenkan"] + df["kijun"]) / 2
    df["senkouB"] = (df["High"].rolling(52).max() + df["Low"].rolling(52).min()) / 2
    df["cloudTop"] = df[["senkouA", "senkouB"]].max(axis=1)
    df["aboveCloud"] = df["Close"] > df["cloudTop"]

    df["earlyBase"] = (
        (df["diplus"] > df["diminus"]) &
        (df["obvUp"]) &
        (df["Close"] > df["ma20"]) &
        (df["Close"] > df["kijun"]) &
        (df["adx"] > 20)
    )

    df["originalEarlyBuy"] = (
        df["earlyBase"] &
        (~df["earlyBase"].shift(1).fillna(False)) &
        (df["Close"] > df["High"].shift(1))
    )

    df["preBuySignal"] = (
        (df["diplus"] < df["diminus"]) &
        (df["diplus"] > df["diplus"].shift(1)) &
        (df["diminus"] < df["diminus"].shift(1)) &
        (df["obvUp"]) &
        (df["Close"] > df["ma20"]) &
        (df["Close"] > df["kijun"]) &
        (df["adx"] > 18)
    )

    df["sellTrigger1"] = (
        ((df["diplus"] > df["diminus"]) & (df["adx"] < df["adx"].shift(2)) & (df["adx"] > 30)) |
        ((df["Close"] > df["ma20"]) & crossunder(df["Close"], df["ma5"]))
    )
    df["sellTrigger1Once"] = df["sellTrigger1"] & (~df["sellTrigger1"].shift(1).fillna(False))

    df["sellTrigger2"] = (
        crossunder(df["Close"], df["ma20"]) |
        crossunder(df["Close"], df["cloudTop"])
    )
    df["sellTrigger2Once"] = df["sellTrigger2"] & (~df["sellTrigger2"].shift(1).fillna(False))

    df["sellTrigger3"] = (
        crossunder(df["diplus"], df["diminus"]) |
        crossunder(df["Close"], df["kijun"])
    )
    df["sellTrigger3Once"] = df["sellTrigger3"] & (~df["sellTrigger3"].shift(1).fillna(False))

    df["BUY"] = False
    df["E_BUY"] = False
    df["SELL_1_3"] = False
    df["SELL_1_2"] = False
    df["FULL_SELL"] = False

    canSell = False
    sellStep = 0
    buyBarIndex = None
    buyPrice = None
    fakeBuyBlockPrice = None

    for i in range(len(df)):
        close = float(df["Close"].iloc[i])

        fakeBuyPriceZone = (
            fakeBuyBlockPrice is not None and
            close >= fakeBuyBlockPrice * (1 - FAKE_BUY_BLOCK_PCT / 100) and
            close <= fakeBuyBlockPrice * (1 + FAKE_BUY_BLOCK_PCT / 100)
        )

        mainBuyCondition = (
            bool(crossover(df["diplus"], df["diminus"]).iloc[i]) and
            bool(df["obvUp"].iloc[i]) and
            bool(df["Close"].iloc[i] > df["ma20"].iloc[i]) and
            bool(df["aboveCloud"].iloc[i]) and
            not fakeBuyPriceZone
        )

        earlyBuyCondition = (
            (bool(df["originalEarlyBuy"].iloc[i]) or bool(df["preBuySignal"].iloc[i])) and
            not canSell and
            not fakeBuyPriceZone and
            not mainBuyCondition
        )

        buySignal = mainBuyCondition or earlyBuyCondition

        buyPlot = False
        earlyBuyPlot = False
        sellSignal1 = False
        sellSignal2 = False
        sellSignal3 = False

        if buySignal and not canSell:
            canSell = True
            sellStep = 0
            buyBarIndex = i
            buyPrice = close

            if mainBuyCondition:
                buyPlot = True
                fakeBuyBlockPrice = None
            else:
                earlyBuyPlot = True

        barsAfterBuy = i - buyBarIndex if buyBarIndex is not None else None

        fastFullSell = (
            canSell and
            not buySignal and
            barsAfterBuy is not None and
            barsAfterBuy > 0 and
            barsAfterBuy <= 3 and
            bool(df["sellTrigger3Once"].iloc[i])
        )

        sellTrigger1Valid = (
            bool(df["sellTrigger1Once"].iloc[i]) and
            buyPrice is not None and
            close > buyPrice
        )

        if fastFullSell:
            sellSignal3 = True
            sellStep = 3
            canSell = False
            fakeBuyBlockPrice = buyPrice

        elif canSell and not buySignal and sellStep <= 2 and bool(df["sellTrigger3Once"].iloc[i]):
            sellSignal3 = True
            sellStep = 3
            canSell = False

        elif canSell and not buySignal and sellStep <= 1 and bool(df["sellTrigger2Once"].iloc[i]):
            sellSignal2 = True
            sellStep = 2

        elif canSell and not buySignal and sellStep == 0 and sellTrigger1Valid:
            sellSignal1 = True
            sellStep = 1

        df.at[df.index[i], "BUY"] = buyPlot
        df.at[df.index[i], "E_BUY"] = earlyBuyPlot
        df.at[df.index[i], "SELL_1_3"] = sellSignal1
        df.at[df.index[i], "SELL_1_2"] = sellSignal2
        df.at[df.index[i], "FULL_SELL"] = sellSignal3

    return df


print("=== 실행 시작 ===")
print("WEBHOOK_URL 있음:", bool(WEBHOOK_URL))
print("MARKET_MODE:", MARKET_MODE)

TICKERS = build_tickers_by_mode()
print("총 스캔 종목 수:", len(TICKERS))

all_latest_signals = []

for ticker, name in TICKERS.items():
    try:
        print("스캔 중:", ticker, name)

        df = yf.download(
            ticker,
            period=PERIOD,
            interval=INTERVAL,
            progress=False,
            auto_adjust=False
        )

        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        if df.empty:
            print(f"{ticker} 데이터 없음")
            continue

        if len(df) < 80:
            print(f"{ticker} 데이터 부족:", len(df))
            continue

        df = calculate_signals(df)
        last_rows = df.tail(SIGNAL_LOOKBACK_DAYS)

        for idx, row in last_rows.iterrows():
            signal = None

            if bool(row["E_BUY"]):
                signal = "E-BUY"
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

                print(f"신호 발견: {signal} {name} {ticker}")

    except Exception as e:
        print(f"종목 처리 오류 {ticker} {name}: {e}")
        continue


print("발견 신호 수:", len(all_latest_signals))

if all_latest_signals:
    msg = f"🚨 [{MARKET_MODE}] 시장 자동 스캔 결과\n"

    for s in all_latest_signals:
        news = "\n".join([f"• {t}" for t in get_news_titles(s["name"], s["ticker"])])

        msg += (
            f"\n[{s['signal']}] {s['name']} ({s['ticker']})\n"
            f"가격: {float(s['close']):.2f}\n"
            f"뉴스:\n{news}\n"
        )

    send_discord(msg)

else:
    send_discord(f"✅ [{MARKET_MODE}] 스캔 완료: 특이 신호 없음")

print("=== 실행 종료 ===")
