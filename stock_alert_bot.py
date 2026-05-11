import yfinance as yf
import pandas as pd
import numpy as np
import requests
from pykrx import stock
from datetime import datetime, timedelta

WEBHOOK_URL = "https://discord.com/api/webhooks/1503073387428446519/RNFbVwgOreGt4Hc708en5oh81_lEfG78YHb_PrUhgUGcin6CBu9Oslf-xIziv34ON1Ky"

PERIOD = "1y"
INTERVAL = "1d"
FAKE_BUY_BLOCK_PCT = 5.0

KR_TOP_N = 80
US_TOP_N = 80

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
    print("자동 종목 수집 중...")

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


def crossover(a, b):
    return (a > b) & (a.shift(1) <= b.shift(1))


def crossunder(a, b):
    return (a < b) & (a.shift(1) >= b.shift(1))


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

    df["tenkan"] = (df["High"].rolling(9).max() + df["Low"].rolling(9).min()) / 2
    df["kijun"] = (df["High"].rolling(26).max() + df["Low"].rolling(26).min()) / 2
    df["senkouA"] = (df["tenkan"] + df["kijun"]) / 2
    df["senkouB"] = (df["High"].rolling(52).max() + df["Low"].rolling(52).min()) / 2

    df["cloudTop"] = df[["senkouA", "senkouB"]].max(axis=1)
    df["aboveCloud"] = df["Close"] > df["cloudTop"]

    high = df["High"]
    low = df["Low"]
    close = df["Close"]

    up_move = high.diff()
    down_move = -low.diff()

    plus_dm = np.where(
        (up_move > down_move) & (up_move > 0),
        up_move,
        0
    )

    minus_dm = np.where(
        (down_move > up_move) & (down_move > 0),
        down_move,
        0
    )

    tr1 = high - low
    tr2 = abs(high - close.shift(1))
    tr3 = abs(low - close.shift(1))
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    atr = tr.ewm(alpha=1 / 14, adjust=False).mean()

    plus_di = (
        100 *
        pd.Series(plus_dm, index=df.index).ewm(alpha=1 / 14, adjust=False).mean()
        / atr
    )

    minus_di = (
        100 *
        pd.Series(minus_dm, index=df.index).ewm(alpha=1 / 14, adjust=False).mean()
        / atr
    )

    dx = 100 * abs(plus_di - minus_di) / (plus_di + minus_di)
    adx = dx.ewm(alpha=1 / 14, adjust=False).mean()

    df["diplus"] = plus_di
    df["diminus"] = minus_di
    df["adx"] = adx

    return df


def calculate_signals(df):
    df = calculate_indicators(df)

    # ======================
    # BUY 조건
    # ======================

    df["earlyBase"] = (
        (df["diplus"] > df["diminus"]) &
        (df["obvUp"]) &
        (df["Close"] > df["ma20"]) &
        (df["Close"] > df["kijun"]) &
        (df["adx"] > 20)
    )

    # 기존 E-BUY
    df["originalEarlyBuy"] = (
        df["earlyBase"] &
        (~df["earlyBase"].shift(1).fillna(False)) &
        (df["Close"] > df["High"].shift(1))
    )

    # 빠른 E-BUY 전조 신호
    df["preBuySignal"] = (
        (df["diplus"] < df["diminus"]) &
        (df["diplus"] > df["diplus"].shift(1)) &
        (df["diminus"] < df["diminus"].shift(1)) &
        (df["obvUp"]) &
        (df["Close"] > df["ma20"]) &
        (df["Close"] > df["kijun"]) &
        (df["adx"] > 18)
    )

    # 강한 BUY
    df["rawMainBuy"] = (
        crossover(df["diplus"], df["diminus"]) &
        df["obvUp"] &
        (df["Close"] > df["ma20"]) &
        df["aboveCloud"]
    )

    # ======================
    # SELL 조건
    # ======================

    df["sellTrigger1"] = (
        (
            (df["diplus"] > df["diminus"]) &
            (df["adx"] < df["adx"].shift(2)) &
            (df["adx"] > 30)
        ) |
        (
            (df["Close"] > df["ma20"]) &
            crossunder(df["Close"], df["ma5"])
        )
    )

    df["sellTrigger1Once"] = (
        df["sellTrigger1"] &
        (~df["sellTrigger1"].shift(1).fillna(False))
    )

    df["sellTrigger2"] = (
        crossunder(df["Close"], df["ma20"]) |
        crossunder(df["Close"], df["cloudTop"])
    )

    df["sellTrigger2Once"] = (
        df["sellTrigger2"] &
        (~df["sellTrigger2"].shift(1).fillna(False))
    )

    df["sellTrigger3"] = (
        crossunder(df["diplus"], df["diminus"]) |
        crossunder(df["Close"], df["kijun"])
    )

    df["sellTrigger3Once"] = (
        df["sellTrigger3"] &
        (~df["sellTrigger3"].shift(1).fillna(False))
    )

    mainSellStep = 0
    mainCanSell = False
    mainBuyBarIndex = None
    mainBuyPrice = None

    earlySellStep = 0
    earlyCanSell = False
    earlyBuyBarIndex = None
    earlyBuyPrice = None

    fakeBuyBlockPrice = None
    signals = []

    for i in range(len(df)):
        row = df.iloc[i]
        close_now = row["Close"]

        fakeBuyPriceZone = (
            fakeBuyBlockPrice is not None and
            close_now >= fakeBuyBlockPrice * (1 - FAKE_BUY_BLOCK_PCT / 100) and
            close_now <= fakeBuyBlockPrice * (1 + FAKE_BUY_BLOCK_PCT / 100)
        )

        originalEarlyBuy = (
            bool(row["originalEarlyBuy"])
            if pd.notna(row["originalEarlyBuy"])
            else False
        )

        preBuySignal = (
            bool(row["preBuySignal"])
            if pd.notna(row["preBuySignal"])
            else False
        )

        rawMainBuy = (
            bool(row["rawMainBuy"])
            if pd.notna(row["rawMainBuy"])
            else False
        )

        mainBuyCondition = (
            rawMainBuy and
            not fakeBuyPriceZone
        )

        earlyBuyCondition = (
            (originalEarlyBuy or preBuySignal) and
            not mainCanSell and
            not earlyCanSell and
            not fakeBuyPriceZone and
            not mainBuyCondition
        )

        mainSignal = ""
        earlySignal = ""

        # ======================
        # BUY 상태 처리
        # ======================

        if earlyBuyCondition:
            earlySellStep = 0
            earlyCanSell = True
            earlyBuyBarIndex = i
            earlyBuyPrice = close_now
            earlySignal = "E-BUY"

        if mainBuyCondition:
            mainSellStep = 0
            mainCanSell = True
            mainBuyBarIndex = i
            mainBuyPrice = close_now
            mainSignal = "BUY"

            earlySellStep = 0
            earlyCanSell = False
            earlyBuyBarIndex = None
            earlyBuyPrice = None
            earlySignal = ""

            fakeBuyBlockPrice = None

        sell1 = (
            bool(row["sellTrigger1Once"])
            if pd.notna(row["sellTrigger1Once"])
            else False
        )

        sell2 = (
            bool(row["sellTrigger2Once"])
            if pd.notna(row["sellTrigger2Once"])
            else False
        )

        sell3 = (
            bool(row["sellTrigger3Once"])
            if pd.notna(row["sellTrigger3Once"])
            else False
        )

        # ======================
        # 메인 BUY SELL 로직
        # ======================

        mainBarsAfterBuy = (
            i - mainBuyBarIndex
            if mainBuyBarIndex is not None
            else None
        )

        mainFastFullSell = (
            mainCanSell and
            not mainBuyCondition and
            mainBarsAfterBuy is not None and
            mainBarsAfterBuy > 0 and
            mainBarsAfterBuy <= 3 and
            sell3
        )

        mainSellTrigger1Valid = (
            sell1 and
            mainBuyPrice is not None and
            close_now > mainBuyPrice
        )

        if mainFastFullSell:
            mainSignal = "FULL SELL"
            mainSellStep = 3
            mainCanSell = False
            fakeBuyBlockPrice = mainBuyPrice

        elif mainCanSell and not mainBuyCondition and mainSellStep <= 2 and sell3:
            mainSignal = "FULL SELL"
            mainSellStep = 3
            mainCanSell = False

        elif mainCanSell and not mainBuyCondition and mainSellStep <= 1 and sell2:
            mainSignal = "1/2 SELL"
            mainSellStep = 2

        elif mainCanSell and not mainBuyCondition and mainSellStep == 0 and mainSellTrigger1Valid:
            mainSignal = "1/3 SELL"
            mainSellStep = 1

        # ======================
        # E-BUY SELL 로직
        # ======================

        earlyBarsAfterBuy = (
            i - earlyBuyBarIndex
            if earlyBuyBarIndex is not None
            else None
        )

        earlyFastFullSell = (
            earlyCanSell and
            not earlyBuyCondition and
            earlyBarsAfterBuy is not None and
            earlyBarsAfterBuy > 0 and
            earlyBarsAfterBuy <= 3 and
            sell3
        )

        earlySellTrigger1Valid = (
            sell1 and
            earlyBuyPrice is not None and
            close_now > earlyBuyPrice
        )

        if earlyFastFullSell:
            earlySignal = "E-FULL"
            earlySellStep = 3
            earlyCanSell = False
            fakeBuyBlockPrice = earlyBuyPrice

        elif earlyCanSell and not earlyBuyCondition and earlySellStep <= 2 and sell3:
            earlySignal = "E-FULL"
            earlySellStep = 3
            earlyCanSell = False

        elif earlyCanSell and not earlyBuyCondition and earlySellStep <= 1 and sell2:
            earlySignal = "E-1/2"
            earlySellStep = 2

        elif earlyCanSell and not earlyBuyCondition and earlySellStep == 0 and earlySellTrigger1Valid:
            earlySignal = "E-1/3"
            earlySellStep = 1

        signals.append({
            "date": df.index[i],
            "close": close_now,
            "mainSignal": mainSignal,
            "earlySignal": earlySignal
        })

    return df, pd.DataFrame(signals)


TICKERS = build_auto_tickers()
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
            print(f"데이터 없음: {ticker}")
            continue

        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        df = df.dropna()

        if len(df) < 60:
            print(f"데이터 부족: {ticker}")
            continue

        _, signal_df = calculate_signals(df)

        last = signal_df.iloc[-1]

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

        line = (
            f"[{signal_text}]\n"
            f"종목: {s['name']} ({s['ticker']})\n"
            f"가격: {s['close']:.2f}\n"
            f"날짜: {s['date'].strftime('%Y-%m-%d')}\n"
        )

        if "BUY" in signal_text:
            buy_msgs.append(line)
        else:
            sell_msgs.append(line)

    msg = "🚨 자동 스캐너 신호 발생\n\n"

    if buy_msgs:
        msg += "🟢 매수 후보\n\n" + "\n".join(buy_msgs) + "\n"

    if sell_msgs:
        msg += "🔴 매도 신호\n\n" + "\n".join(sell_msgs)

    send_discord(msg)
    print(msg)

else:
    msg = "✅ 자동 스캐너 실행 완료\n마지막 봉 기준 신규 신호 없음"
    send_discord(msg)
    print(msg)
