import os
import io
import time
import requests
import numpy as np
import pandas as pd
import yfinance as yf
import FinanceDataReader as fdr
from concurrent.futures import ThreadPoolExecutor

# 환경 변수 및 설정
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
MARKET_MODE = os.getenv("MARKET_MODE", "ALL").upper().replace("-", "_")
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36'
}

# S&P500 / NASDAQ 주요 핵심 대형 우량주 (모든 네트워크 통신 실패 시 작동하는 최후의 80개 보루)
FALLBACK_US = [
    "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "TSLA", "AVGO", "COST", "PEP",
    "CSCO", "ADBE", "AMD", "QCOM", "NFLX", "TXN", "AMGN", "INTU", "ISRG", "HON",
    "AMAT", "BKNG", "CMCSA", "VRTX", "MU", "REGN", "LRCX", "ADI", "PANW", "MDLZ",
    "MELI", "SNPS", "KLAC", "CDNS", "ASML", "CSX", "MAR", "ORLY", "CTAS", "NXPI",
    "WDAY", "ROP", "PCAR", "MNST", "PAYX", "ROST", "ADSK", "LULU", "KDP", "GILD",
    "ODFL", "FAST", "MCHP", "CPRT", "SBUX", "MRVL", "CTSH", "AEP", "PDD", "IDXX",
    "EXC", "AZN", "BKR", "FTNT", "EA", "ALGN", "GEHC", "KHC", "WBA", "ILMN",
    "CEG", "VST", "PLTR", "CRM", "NOW", "SNOW", "DDOG", "NET", "TEAM", "MDB", "TSM"
]

TICKER_NAMES = {
    "AAPL": "애플", "MSFT": "마이크로소프트", "NVDA": "엔비디아", "GOOGL": "구글",
    "AMZN": "아마존", "META": "메타", "TSLA": "테슬라", "AMD": "AMD",
    "NFLX": "넷플릭스", "PLTR": "팔란티어", "INTC": "인텔"
}

THEMES = {
    "AI": {"NVDA", "MSFT", "GOOGL", "META", "AMZN", "PLTR", "AMD"},
    "SEMICON": {"AVGO", "INTC", "TSM", "QCOM", "MU", "ASML"},
    "POWER": {"CEG", "VST", "NEE", "DUK", "SO", "ETN", "GE"},
    "CLOUD": {"AMZN", "MSFT", "GOOGL", "SNOW", "CRM", "NOW"}
}

def send_discord(msg):
    if not WEBHOOK_URL:
        print(msg)
        return
    try:
        requests.post(WEBHOOK_URL, json={"content": msg}, timeout=10)
    except Exception as e:
        print("Discord error:", e)

def get_us():
    global TICKER_NAMES
    tickers = []
    
    # 1. FinanceDataReader를 통한 S&P 500 로드 (가장 안정적이고 차단 위험 없음)
    try:
        print("🔍 FinanceDataReader를 통해 S&P 500 종목 로드 중...")
        df_sp = fdr.StockListing('S&P500')
        if not df_sp.empty:
            for _, row in df_sp.iterrows():
                t = str(row.get('Symbol', '')).strip()
                n = str(row.get('Name', '')).strip()
                if t:
                    tickers.append(t)
                    if t not in TICKER_NAMES:
                        TICKER_NAMES[t] = n
            print(f"✅ FDR S&P 500 로드 성공: {len(tickers)}개 확보")
    except Exception as e:
        print(f"⚠️ FDR S&P 500 로드 실패 ({e}). 백업 절차를 준비합니다.")

    # 2. 필수 테마 및 핵심 기술주 (S&P500에 누락되었거나 ADR인 반도체/클라우드 등) 강제 편입
    core_tech_nasdaq = [
        "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "TSLA", "AVGO", "COST", "PEP",
        "CSCO", "ADBE", "AMD", "QCOM", "NFLX", "TXN", "AMGN", "INTU", "ISRG", "HON",
        "AMAT", "BKNG", "CMCSA", "VRTX", "MU", "REGN", "LRCX", "ADI", "PANW", "MDLZ",
        "MELI", "SNPS", "KLAC", "CDNS", "ASML", "CSX", "MAR", "ORLY", "CTAS", "NXPI",
        "WDAY", "ROP", "PCAR", "MNST", "PAYX", "ROST", "ADSK", "LULU", "KDP", "GILD",
        "ODFL", "FAST", "MCHP", "CPRT", "SBUX", "MRVL", "CTSH", "AEP", "PDD", "IDXX",
        "EXC", "AZN", "BKR", "FTNT", "EA", "ALGN", "GEHC", "KHC", "WBA", "ILMN",
        "CEG", "VST", "PLTR", "CRM", "NOW", "SNOW", "DDOG", "NET", "TEAM", "MDB", "TSM"
    ]
    
    # 설정된 테마 사전(THEMES)에 정의된 주식들이 스캔 리스트에 반드시 존재하도록 확인
    for theme, t_set in THEMES.items():
        for t in t_set:
            if t not in core_tech_nasdaq:
                core_tech_nasdaq.append(t)
                
    # 종목 병합 및 한글/영문 이름 매핑 테이블 초기화
    for t in core_tech_nasdaq:
        if t not in tickers:
            tickers.append(t)
        if t not in TICKER_NAMES:
            TICKER_NAMES[t] = t  # 매핑된 한글명이 없다면 티커명을 기본으로 지정

    # 3. 네트워크가 완전히 마비되었을 경우 최후의 비상 수단
    if not tickers:
        print("🚨 모든 네트워크 로딩 실패. 80개 우량주 기본 리스트를 불러옵니다.")
        return FALLBACK_US

    unique_tickers = list(set(tickers))
    print(f"🚀 미국 주식 총 {len(unique_tickers)}개 종목 스캔 준비 완료!")
    return unique_tickers

def get_kr():
    global TICKER_NAMES
    try:
        df_krx = fdr.StockListing('KRX')
        top300 = df_krx.sort_values(by='Marcap', ascending=False).head(300)
        tickers = []
        for _, row in top300.iterrows():
            market_suffix = ".KS" if row['Market'] == 'KOSPI' else ".KQ"
            full_ticker = f"{row['Code']}{market_suffix}"
            tickers.append(full_ticker)
            TICKER_NAMES[full_ticker] = str(row['Name']).strip()
        return tickers
    except:
        return []

# 트레이딩뷰 ta.rma (Running Moving Average) 구현 함수
def rma(series, length):
    return series.ewm(alpha=1/length, min_periods=length, adjust=False).mean()

# 크로스오버 / 크로스언더 구현 함수
def crossover(s1, s2):
    return (s1 > s2) & (s1.shift(1) <= s2.shift(1))

def crossunder(s1, s2):
    return (s1 < s2) & (s1.shift(1) >= s2.shift(1))

def analyze_ticker(ticker):
    global TICKER_NAMES
    try:
        # yfinance 다운로드 안정성 극대화를 위한 최대 3회 자동 재시도 루프
        df = pd.DataFrame()
        for attempt in range(3):
            try:
                df = yf.download(ticker, period="1y", interval="1d", progress=False)
                if not df.empty and len(df) >= 60:
                    break
            except Exception:
                pass
            time.sleep(1.0) # 일시적 오류 방지를 위해 재시도 전 1초 대기
            
        if df.empty or len(df) < 60: return None
        
        # 1D Series 데이터 정리 및 MultiIndex 구조 완벽 대응
        close_data = df["Close"].iloc[:, 0] if len(df["Close"].shape) > 1 else df["Close"]
        high_data = df["High"].iloc[:, 0] if len(df["High"].shape) > 1 else df["High"]
        low_data = df["Low"].iloc[:, 0] if len(df["Low"].shape) > 1 else df["Low"]
        
        close_data = pd.Series(close_data).ffill().dropna().astype(float)
        high_data = pd.Series(high_data).ffill().dropna().astype(float)
        low_data = pd.Series(low_data).ffill().dropna().astype(float)
        
        if len(close_data) < 60: return None
        
        # 1. 기본 이평선 계산
        ma20 = close_data.rolling(20).mean()
        
        # 2. DMI (14) 계산 (트레이딩뷰 rma 방식)
        up = high_data.diff()
        down = -low_data.diff()
        plus_dm = np.where((up > down) & (up > 0), up, 0)
        minus_dm = np.where((down > up) & (down > 0), down, 0)
        
        tr1 = high_data - low_data
        tr2 = abs(high_data - close_data.shift(1))
        tr3 = abs(low_data - close_data.shift(1))
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        
        tr_smooth = rma(tr, 14)
        plus_dm_smooth = rma(pd.Series(plus_dm, index=close_data.index), 14)
        minus_dm_smooth = rma(pd.Series(minus_dm, index=close_data.index), 14)
        
        diplus = 100 * plus_dm_smooth / tr_smooth
        diminus = 100 * minus_dm_smooth / tr_smooth
        
        # 3. 일목균형표 구름대 계산
        tenkan = (high_data.rolling(9).max() + low_data.rolling(9).min()) / 2
        kijun = (high_data.rolling(26).max() + low_data.rolling(26).min()) / 2
        senkouA = (tenkan + kijun) / 2
        senkouB = (high_data.rolling(52).max() + low_data.rolling(52).min()) / 2
        cloudTop = np.maximum(senkouA, senkouB)
        
        # 4. 슈퍼트렌드 (10, 3.0) 계산 (트레이딩뷰 기본 공식 이식)
        st_atr = rma(tr, 10)
        hl2 = (high_data + low_data) / 2
        basic_ub = hl2 + 3.0 * st_atr
        basic_lb = hl2 - 3.0 * st_atr
        
        final_ub = basic_ub.copy()
        final_lb = basic_lb.copy()
        direction = np.ones(len(close_data)) # 1: 하락, -1: 상승
        
        for i in range(1, len(close_data)):
            final_ub.iloc[i] = basic_ub.iloc[i] if basic_ub.iloc[i] < final_ub.iloc[i-1] or close_data.iloc[i-1] > final_ub.iloc[i-1] else final_ub.iloc[i-1]
            final_lb.iloc[i] = basic_lb.iloc[i] if basic_lb.iloc[i] > final_lb.iloc[i-1] or close_data.iloc[i-1] < final_lb.iloc[i-1] else final_lb.iloc[i-1]
            
            if direction[i-1] == -1:
                direction[i] = 1 if close_data.iloc[i] < final_lb.iloc[i] else -1
            else:
                direction[i] = -1 if close_data.iloc[i] > final_ub.iloc[i] else 1

        # 5. 트레이딩뷰 내부 상태 머신(State Machine) 시뮬레이션 루프
        tradeActive = False
        sellStep = 0
        buyBarIndex = None
        buyPrice = None
        fakeBuyBlockPrice = None
        fakeBuyBlockPct = 5.0
        
        cond_di_cross = crossover(diplus, diminus)
        cond_obv_up = close_data > close_data.shift(1) # obv > obv[1] 과 동치
        cond_above_ma20 = close_data > ma20
        cond_above_cloud = close_data > cloudTop
        
        crossunder_close_ma20 = crossunder(close_data, ma20)
        crossunder_close_cloudTop = crossunder(close_data, cloudTop)
        crossunder_di = crossunder(diplus, diminus)
        crossunder_close_kijun = crossunder(close_data, kijun)
        
        sellTriggerOneThird = (crossunder_close_ma20 | crossunder_close_cloudTop)
        sellTriggerHalf = (crossunder_di | crossunder_close_kijun)
        
        mainBuyPlot = np.zeros(len(close_data), dtype=bool)
        stBuyPlot = np.zeros(len(close_data), dtype=bool)
        
        for i in range(1, len(close_data)):
            c_close = close_data.iloc[i]
            
            fakeBuyPriceZone = False
            if fakeBuyBlockPrice is not None:
                fakeBuyPriceZone = (c_close >= fakeBuyBlockPrice * (1 - fakeBuyBlockPct / 100)) and (c_close <= fakeBuyBlockPrice * (1 + fakeBuyBlockPct / 100))
                
            mainBuyCondition = cond_di_cross.iloc[i] and cond_obv_up.iloc[i] and cond_above_ma20.iloc[i] and cond_above_cloud.iloc[i] and not fakeBuyPriceZone
            trendBuyCondition = (direction[i] == -1 and direction[i-1] == 1)
            
            buyAllowed = not tradeActive or sellStep == 1
            buySignal = (mainBuyCondition or trendBuyCondition) and buyAllowed
            
            sellTriggerOneThirdOnce = sellTriggerOneThird.iloc[i] and not sellTriggerOneThird.iloc[i-1]
            sellTriggerHalfOnce = sellTriggerHalf.iloc[i] and not sellTriggerHalf.iloc[i-1]
            superTrendFullSell = (direction[i] == 1 and direction[i-1] == -1)
            
            current_mainBuyPlot = False
            current_stBuyPlot = False
            
            if buySignal:
                tradeActive = True
                sellStep = 0
                buyBarIndex = i
                buyPrice = c_close
                if mainBuyCondition:
                    fakeBuyBlockPrice = None
                current_mainBuyPlot = mainBuyCondition
                current_stBuyPlot = trendBuyCondition and not mainBuyCondition
            
            barsAfterBuy = (i - buyBarIndex) if buyBarIndex is not None else None
            fastHalfSell = tradeActive and not buySignal and barsAfterBuy is not None and (0 < barsAfterBuy <= 3) and sellTriggerHalfOnce
            
            if tradeActive and not buySignal and superTrendFullSell:
                sellStep = 3
                tradeActive = False
                fakeBuyBlockPrice = buyPrice
            elif tradeActive and not buySignal and sellStep <= 1 and (fastHalfSell or sellTriggerHalfOnce):
                sellStep = 2
                fakeBuyBlockPrice = buyPrice
            elif tradeActive and not buySignal and sellStep == 0 and sellTriggerOneThirdOnce:
                sellStep = 1
                
            mainBuyPlot[i] = current_mainBuyPlot
            stBuyPlot[i] = current_stBuyPlot

        # 6. 최종 '오늘(마지막 바)' 발생한 신호만 필터링
        if mainBuyPlot[-1]:
            signal = "🔥 MAIN BUY (핵심 기술지표 결합 매수 신호)"
        elif stBuyPlot[-1]:
            signal = "🔵 ST BUY (슈퍼트렌드 상승 전환 매수 신호)"
        else:
            return None
            
        matched = [t for t, tickers in THEMES.items() if ticker.split('.')[0] in tickers]
        tag = f" [{', '.join(matched)}]" if matched else ""
        stock_name = TICKER_NAMES.get(ticker, ticker)
        price = float(close_data.iloc[-1])
        
        return f"📊 **{stock_name}** ({ticker}){tag}\n💬 신호: {signal}\n💰 현재가: {price:.2f}"
    except Exception as e:
        # 디버깅 및 예외 로깅을 위한 로컬 출력
        print(f"Error analyzing {ticker}: {e}")
        return None

def run():
    universe = []
    if MARKET_MODE in ["US", "ALL"]: universe += get_us()
    if MARKET_MODE in ["KR", "ALL"]: universe += get_kr()
    
    if not universe:
        send_discord("⚠️ 감시 대상 종목을 불러올 수 없습니다.")
        return

    print(f"🚀 스캔 시작: {len(universe)}개 종목 (GitHub Actions 안정화 버전)")
    
    results = []
    # 요청 속도 및 병렬 처리 밸런스를 고려하여 max_workers 유지 및 안정적 인터벌 배치
    with ThreadPoolExecutor(max_workers=5) as executor:
        for res in executor.map(analyze_ticker, universe):
            if res:
                results.append(res)
            time.sleep(0.15) 

    if not results:
        send_discord(f"📉 조건에 맞는 신호 없음 (총 {len(universe)}개 종목 스캔 완료)")
    else:
        msg = f"🚀 **주식 스캐너 알림 ({MARKET_MODE})**\n\n"
        for res in results:
            if len(msg) + len(res) > 1850:
                send_discord(msg)
                msg = ""
            msg += res + "\n\n----------------\n\n"
        send_discord(msg)

if __name__ == "__main__":
    run()
