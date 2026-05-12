```python
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

        # 언론사 제거
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


def get_us_sp500_top_trading_value(top_n=80):
    try:
        url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
        table = pd.read_html(url)[0]
        symbols = table["Symbol"].str.replace(".", "-", regex=False).tolist()

        candidates = {}

        for symbol in symbols:
            try:
                df = yf.download(
                    symbol,
                    period="1mo",
                    interval="1d",
                    auto_adjust=False,
                    progress=False
                )

                if df.empty:
                    continue

                if isinstance(df.columns, pd.MultiIndex):
                    df.columns = df.columns.get_level_values(0)

                last = df.dropna().iloc[-1]
                trading_value = float(last["Close"] * last["Volume"])
                candidates[symbol] = trading_value

            except Exception:
                continue

        sorted_items = sorted(
            candidates.items(),
            key=lambda x: x[1],
            reverse=True
        )[:top_n]

        return {symbol: symbol for symbol, _ in sorted_items}

    except Exception as e:
        print(f"미국 종목 자동수집 오류: {e}")
        return {}


def build_auto_tickers():
    print("전체 자동 종목 수집 중...")

    kr = get_kr_top_trading_value(KR_TOP_N)
    us = get_us_sp500_top_trading_value(US_TOP_N)

    tickers = {}
    tickers.update(kr)
    tickers.update(us)
    tickers.update(CRYPTO_TICKERS)

    print(f"국내 자동수집: {len(kr)}개")
    print(f"미국 자동수집: {len(us)}개")
    print(f"코인 추가: {len(CRYPTO_TICKERS)}개")
    print(f"총 감시 종목: {len(tickers)}개")

    return tickers


def build_tickers_by_mode():
    if MARKET_MODE == "KR":
        print("국장 모드 실행")
        return get_kr_top_trading_value(KR_TOP_N)

    elif MARKET_MODE == "US":
        print("미장 모드 실행")
        return get_us_sp500_top_trading_value(US_TOP_N)

    elif MARKET_MODE == "CRYPTO":
        print("코인 모드 실행")
        return CRYPTO_TICKERS

    else:
        print("전체 모드 실행")
        return build_auto_tickers()


def crossover(a, b):
    return (a > b) & (a.shift(1) <= b.shift(1))


def crossunder(a, b):
    return (a < b) & (a.shift(1) >= b.shift(1))


# =========================
# 기존 calculate_indicators
# 기존 calculate_signals
# 그대로 유지
# =========================

# 네 기존 코드 그대로 붙이면 됨


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

        _, signal_df = calculate_signals(df)

        recent_signals = signal_df.tail(SIGNAL_LOOKBACK_DAYS)

        for _, last in recent_signals.iterrows():
            if last["mainSignal"] or last["earlySignal"]:
                all_latest_signals.append({
                    "ticker": ticker,
                    "name": name,
                    "date": last["date"],
                    "close": last["close"],
                    "mainSignal": last["mainSignal"],
                    "earlySignal": last["earlySignal"]
                })

    except Exception as e:
        print(f"{ticker} 오류: {e}")


if all_latest_signals:
    buy_msgs = []
    sell_msgs = []

    for s in all_latest_signals:
        signal_text = s["mainSignal"] if s["mainSignal"] else s["earlySignal"]

        news_titles = get_news_titles(s['name'])

        news_text = "\n".join(
            [f"• {title}" for title in news_titles]
        )

        line = (
            f"[{signal_text}]\n"
            f"종목: {s['name']} ({s['ticker']})\n"
            f"가격: {s['close']:.2f}\n"
            f"날짜: {s['date'].strftime('%Y-%m-%d')}\n\n"
            f"최근 뉴스\n"
            f"{news_text}\n"
        )

        if "BUY" in signal_text:
            buy_msgs.append(line)
        else:
            sell_msgs.append(line)

    msg = f"🚨 자동 스캐너 신호 발생 - {MARKET_MODE} 모드\n\n"

    if buy_msgs:
        msg += "🟢 매수 후보\n\n" + "\n".join(buy_msgs) + "\n"

    if sell_msgs:
        msg += "🔴 매도 신호\n\n" + "\n".join(sell_msgs)

    send_discord(msg)
    print(msg)

else:
    msg = (
        f"✅ 자동 스캐너 실행 완료 - {MARKET_MODE} 모드\n"
        f"최근 {SIGNAL_LOOKBACK_DAYS}봉 기준 신규 신호 없음"
    )

    send_discord(msg)
    print(msg)
```
