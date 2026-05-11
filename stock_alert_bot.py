import yfinance as yf
import pandas as pd
import numpy as np
import requests

WEBHOOK_URL = "여기에_디스코드_웹후크_URL"

US_TICKERS = {
    "NVDA": "엔비디아",
    "MSFT": "마이크로소프트",
    "AAPL": "애플",
    "AMZN": "아마존",
    "GOOGL": "구글",
    "META": "메타",
    "TSLA": "테슬라",
    "AMD": "AMD",
    "AVGO": "브로드컴",
    "PLTR": "팔란티어",
    "SMCI": "슈퍼마이크로",
    "NFLX": "넷플릭스",
    "QQQ": "나스닥100 ETF",
    "SPY": "S&P500 ETF",
}

KR_TICKERS = {
    "005930.KS": "삼성전자",
    "000660.KS": "SK하이닉스",
    "035420.KS": "NAVER",
    "035720.KS": "카카오",
    "005380.KS": "현대차",
    "000270.KS": "기아",
    "068270.KS": "셀트리온",
    "207940.KS": "삼성바이오로직스",
    "373220.KS": "LG에너지솔루션",
    "051910.KS": "LG화학",
    "006400.KS": "삼성SDI",
    "247540.KQ": "에코프로비엠",
    "086520.KQ": "에코프로",
    "196170.KQ": "알테오젠",
    "028300.KQ": "HLB",
    "277810.KQ": "레인보우로보틱스",
    "112040.KQ": "위메이드",
    "263750.KQ": "펄어비스",
}

TICKERS = {}
TICKERS.update(US_TICKERS)
TICKERS.update(KR_TICKERS)

PERIOD = "1y"
INTERVAL = "1d"
FAKE_BUY_BLOCK_PCT = 5.0


def send_discord(message):
    requests.post(WEBHOOK_URL, json={"content": message})


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

    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0)

    tr1 = high - low
    tr2 = abs(high - close.shift(1))
    tr3 = abs(low - close.shift(1))
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    atr = tr.ewm(alpha=1/14, adjust=False).mean()
    plus_di = 100 * pd.Series(plus_dm, index=df.index).ewm(alpha=1/14, adjust=False).mean() / atr
    minus_di = 100 * pd.Series(minus_dm, index=df.index).ewm(alpha=1/14, adjust=False).mean() / atr

    dx = 100 * abs(plus_di - minus_di) / (plus_di + minus_di)
    adx = dx.ewm(alpha=1/14, adjust=False).mean()

    df["diplus"] = plus_di
    df["diminus"] = minus_di
    df["adx"] = adx

    return df


def calculate_signals(df):
    df = calculate_indicators(df)

    df["earlyBase"] = (
        (df["diplus"] > df["diminus"]) &
        (df["obvUp"]) &
        (df["Close"] > df["ma20"]) &
        (df["Close"] > df["kijun"]) &
        (df["adx"] > 20)
    )

    df["rawMainBuy"] = (
        crossover(df["diplus"], df["diminus"]) &
        df["obvUp"] &
        (df["Close"] > df["ma20"]) &
        df["aboveCloud"]
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
        high_prev = df["High"].iloc[i-1] if i > 0 else np.nan

        fakeBuyPriceZone = (
            fakeBuyBlockPrice is not None and
            close_now >= fakeBuyBlockPrice * (1 - FAKE_BUY_BLOCK_PCT / 100) and
            close_now <= fakeBuyBlockPrice * (1 + FAKE_BUY_BLOCK_PCT / 100)
        )

        earlyBase_now = bool(row["earlyBase"]) if pd.notna(row["earlyBase"]) else False
        earlyBase_prev = bool(df["earlyBase"].iloc[i-1]) if i > 0 and pd.notna(df["earlyBase"].iloc[i-1]) else False
        rawMainBuy = bool(row["rawMainBuy"]) if pd.notna(row["rawMainBuy"]) else False

        mainBuyCondition = rawMainBuy and not fakeBuyPriceZone

        rawEarlyBuyCondition = (
            earlyBase_now and
            not earlyBase_prev and
            pd.notna(high_prev) and
            close_now > high_prev and
            not mainCanSell and
            not fakeBuyPriceZone
        )

        earlyBuyCondition = rawEarlyBuyCondition and not mainBuyCondition

        mainSignal = ""
        earlySignal = ""

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

        sell1 = bool(row["sellTrigger1Once"]) if pd.notna(row["sellTrigger1Once"]) else False
        sell2 = bool(row["sellTrigger2Once"]) if pd.notna(row["sellTrigger2Once"]) else False
        sell3 = bool(row["sellTrigger3Once"]) if pd.notna(row["sellTrigger3Once"]) else False

        mainBarsAfterBuy = i - mainBuyBarIndex if mainBuyBarIndex is not None else None

        mainFastFullSell = (
            mainCanSell and
            not mainBuyCondition and
            mainBarsAfterBuy is not None and
            mainBarsAfterBuy > 0 and
            mainBarsAfterBuy <= 3 and
            sell3
        )

        mainSellTrigger1Valid = sell1 and mainBuyPrice is not None and close_now > mainBuyPrice

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

        earlyBarsAfterBuy = i - earlyBuyBarIndex if earlyBuyBarIndex is not None else None

        earlyFastFullSell = (
            earlyCanSell and
            not earlyBuyCondition and
            earlyBarsAfterBuy is not None and
            earlyBarsAfterBuy > 0 and
            earlyBarsAfterBuy <= 3 and
            sell3
        )

        earlySellTrigger1Valid = sell1 and earlyBuyPrice is not None and close_now > earlyBuyPrice

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

        _, signal_df = calculate_signals(df)

        recent = signal_df[
            (signal_df["mainSignal"] != "") | (signal_df["earlySignal"] != "")
        ].tail(5)

        print(recent)

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

    msg = "🚨 주식 신호 발생\n\n"

    if buy_msgs:
        msg += "🟢 매수 후보\n\n" + "\n".join(buy_msgs) + "\n"

    if sell_msgs:
        msg += "🔴 매도 신호\n\n" + "\n".join(sell_msgs)

    send_discord(msg)
    print(msg)

else:
    print("마지막 봉 기준 신규 신호 없음")
