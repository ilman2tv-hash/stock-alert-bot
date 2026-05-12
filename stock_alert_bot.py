import yfinance as yf
import pandas as pd
import numpy as np
import requests
import os
import feedparser
from urllib.parse import quote
import re

from pykrx import stock
from datetime import datetime, timedelta

WEBHOOK_URL = "https://discord.com/api/webhooks/1503073387428446519/RNFbVwgOreGt4Hc708en5oh81_lEfG78YHb_PrUhgUGcin6CBu9Oslf-xIziv34ON1Ky"

PERIOD = "1y"
INTERVAL = "1d"
FAKE_BUY_BLOCK_PCT = 5.0

KR_TOP_N = 80
US_TOP_N = 80

MARKET_MODE = os.getenv("MARKET_MODE", "ALL")
SIGNAL_LOOKBACK_DAYS = 3

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
    requests.post(WEBHOOK_URL, json={"content": message})


def get_news_titles(stock_name):
    query = quote(f"{stock_name} 주식")

    rss_url = (
        f"https://news.google.com/rss/search?"
        f"q={query}&hl=ko&gl=KR&ceid=KR:ko"
    )

    feed = feedparser.parse(rss_url)

    if not feed.entries:
        return ["관련 뉴스 없음"]

    news_titles = []

    for entry in feed.entries[:3]:
        title = entry.title
        title = re.sub(r"\s-\s.+$", "", title)
        news_titles.append(title)

    return news_titles


def get_recent_kr_date():
    today = datetime.now()

    for i in range(10):
        d = today - timedelta(days=i)
        date_str = d.strftime("%Y%m%d")

        try:
            df = stock.get_market_cap_by_ticker(date_str, market="KOSPI")
            if not df.empty:
                return date_str
        except Exception:
            pass

    return today.strftime("%Y%m%d")


def get_kr_top_trading_value(top_n=80):
    date_str = get_recent_kr_date()
    result = {}

    for market, suffix in [("KOSPI", ".KS"), ("KOSDAQ", ".KQ")]:
        try:
            df = stock.get_market_cap_by_ticker(date_str, market=market)
            df = df.sort_values("거래대금", ascending=False).head(top_n // 2)

            for code in df.index:
                name = stock.get_market_ticker_name(code)
                result[f"{code}{suffix}"] = name

        except Exception as e:
            print(f"{market} 자동수집 오류: {e}")

    return result


def build_tickers_by_mode():
    if MARKET_MODE == "KR":
        print("국장 모드 실행")
        return get_kr_top_trading_value(KR_TOP_N)

    elif MARKET_MODE == "CRYPTO":
        print("코인 모드 실행")
        return CRYPTO_TICKERS

    else:
        print("전체 모드 실행")
        tickers = {}
        tickers.update(get_kr_top_trading_value(KR_TOP_N))
        tickers.update(CRYPTO_TICKERS)
        return tickers


def calculate_indicators(df):
    df = df.copy()

    df["ma20"] = df["Close"].rolling(20).mean()
    df["ma5"] = df["Close"].rolling(5).mean()

    df["obv_change"] = np.where(
        df["Close"] > df["Close"].shift(1),
        df["Volume"],
        np.where(df["Close"] < df["Close"].shift(1), -df["Volume"], 0)
    )

    df["obv"] = df["obv_change"].cumsum()
    df["obvUp"] = df["obv"] > df["obv"].shift(1)

    return df


def calculate_signals(df):
    df = calculate_indicators(df)

    df["buySignal"] = (
        (df["Close"] > df["ma20"]) &
        (df["obvUp"])
    )

    signals = []

    for i in range(1, len(df)):
        row = df.iloc[i]
        signal = ""

        if row["buySignal"] and not df.iloc[i - 1]["buySignal"]:
            signal = "BUY"

        signals.append({
            "date": df.index[i],
            "close": row["Close"],
            "signal": signal
        })

    return pd.DataFrame(signals)


TICKERS = build_tickers_by_mode()
all_latest_signals = []

for ticker, name in TICKERS.items():
    print(f"검사 중: {ticker} {name}")

    try:
        df = yf.download(
            ticker,
            period=PERIOD,
            interval=INTERVAL,
            auto_adjust=False,
            progress=False
        )

        if df.empty:
            continue

        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        df = df.dropna()

        if len(df) < 60:
            continue

        signal_df = calculate_signals(df)
        recent_signals = signal_df.tail(SIGNAL_LOOKBACK_DAYS)

        for _, last in recent_signals.iterrows():
            if last["signal"]:
                all_latest_signals.append({
                    "ticker": ticker,
                    "name": name,
                    "date": last["date"],
                    "close": last["close"],
                    "signal": last["signal"]
                })

    except Exception as e:
        print(f"{ticker} 오류: {e}")


if all_latest_signals:
    msg = f"🚨 자동 스캐너 신호 발생 - {MARKET_MODE} 모드\n\n"

    for s in all_latest_signals:
        news_titles = get_news_titles(s["name"])
        news_text = "\n".join([f"• {title}" for title in news_titles])

        line = (
            f"[{s['signal']}]\n"
            f"종목: {s['name']} ({s['ticker']})\n"
            f"가격: {s['close']:.2f}\n"
            f"날짜: {s['date'].strftime('%Y-%m-%d')}\n\n"
            f"최근 뉴스\n"
            f"{news_text}\n\n"
        )

        msg += line

    send_discord(msg)
    print(msg)

else:
    msg = (
        f"✅ 자동 스캐너 실행 완료 - {MARKET_MODE} 모드\n"
        f"최근 {SIGNAL_LOOKBACK_DAYS}봉 기준 신규 신호 없음"
    )

    send_discord(msg)
    print(msg)
