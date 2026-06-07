import yfinance as yf
import pandas as pd
import numpy as np
import requests
import os
import time
import json
import feedparser
from urllib.parse import quote
import re
from googletrans import Translator
from pykrx import stock
from datetime import datetime, timedelta

# --- 환경 설정 ---
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
MARKET_MODE = os.getenv("MARKET_MODE", "US_OPTION") # "KR", "US", "ALL", "US_OPTION"

translator = Translator()

PERIOD = "1y"
INTERVAL = "1d"

KR_TOP_N = 400  # 코스피 200개 + 코스닥 200개
US_TOP_N = 600  # S&P 500 + 나스닥 100 

# 슈퍼트렌드 설정
ST_ATR_PERIOD = 10
ST_FACTOR = 3.0

def send_discord(message):
    if not WEBHOOK_URL or "http" not in WEBHOOK_URL:
        return
    try:
        chunks = [message[i:i + 1900] for i in range(0, len(message), 1900)]
        for chunk in chunks:
            requests.post(WEBHOOK_URL, json={"content": chunk}, timeout=15)
    except: pass

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
        vix_status = "안정" if vix_val < 20 else "경계" if vix_val < 30 else "위험"
        
        return (f"📊 시장상황: {status}\n"
                f"🇺🇸 미국시장: {'상승' if spy_risk_on else '약세'}\n"
                f"💻 기술주: {'상승' if qqq_risk_on else '약세'}\n"
                f"😱 공포지수: {vix_val:.2f} ({vix_status})")
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
        # 데이터 3개월치로 확장
        data = yf.download(["SPY", "^VIX", "^PCCR"], period="3mo", interval="1d", progress=False)
        close = data["Close"]
        
        pccr = close["^PCCR"].dropna()
        spy = close["SPY"].dropna()
        vix = close["^VIX"].dropna()

        curr_pccr = float(pccr.iloc[-1])
        curr_spy = float(spy.iloc[-1])
        curr_vix = float(vix.iloc[-1])
        
        prev3_pccr = float(pccr.iloc[-4])
        prev3_spy = float(spy.iloc[-4])
        
        pccr_ma20 = pccr.rolling(window=20).mean().iloc[-1]
        spy_ma20 = spy.rolling(window=20).mean().iloc[-1]

        signals = []

        # 변동률 계산 (최근 3영업일)
        spy_change_pct = (curr_spy - prev3_spy) / prev3_spy * 100
        pccr_change_pct = (curr_pccr - prev3_pccr) / prev3_pccr * 100

        # [1] 🚨 폭락 전조 경계 (주가 기만 + 풋옵션 매집)
        if spy_change_pct > -0.5 and pccr_change_pct > 15.0 and curr_pccr > pccr_ma20:
            signals.append(f"⚠️ **[폭락 전조 경계]** 지수는 버티는데 스마트머니 풋옵션 매집 중!\n"
                           f"▶ 최근 3일 SPY 변동: {spy_change_pct:.2f}% / PCCR 급등: +{pccr_change_pct:.1f}%")
                           
        # [2] 🚀 폭등 전조 경계 (주가 억압 + 콜옵션 매집)
        elif spy_change_pct < 0.5 and pccr_change_pct < -15.0 and curr_pccr < pccr_ma20:
            signals.append(f"🚀 **[상승 전환 포착]** 지수는 눌려있는데 스마트머니 콜옵션 매집 중!\n"
                           f"▶ 최근 3일 SPY 변동: {spy_change_pct:.2f}% / PCCR 급락(콜 우위): {pccr_change_pct:.1f}%")

        # [3] 🧊 하락장 지속 경고 (상승 전환 신호가 없을 때 계속 유지됨)
        if curr_spy < spy_ma20 and curr_vix > 20:
            signals.append(f"🧊 **[하락 추세 진행 중]** SPY가 20일선 아래에 있고 VIX가 높습니다.\n"
                           f"▶ 찐바닥 상승 전환 신호가 뜰 때까지 보수적으로 대응하세요.")

        # [4] 단기 과열/투매 (극단적 역발상 지표)
        if curr_pccr > 1.25 and curr_pccr > pccr_ma20 * 1.3:
            signals.append(f"🟢 **[단기 바닥 가능성]** 투매 절정! 극단적 공포 상태 (PCCR: {curr_pccr:.2f})")
        elif curr_pccr < 0.55 and curr_pccr < pccr_ma20 * 0.7:
            signals.append(f"🔴 **[단기 천장 주의]** 극단적 탐욕! 콜옵션 과열 (PCCR: {curr_pccr:.2f})")

        return "\n".join(signals) if signals else None
    except Exception as e:
        return None

# --- 지표 계산 함수 (기존과 동일, 생략 없이 유지) ---
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
    df["cloudTop"] = df[["senkouA", "senkouB"]].max(axis=1)
    df["kijun"] = (df.High.rolling(26).max() + df.Low.rolling(26).min()) / 2

    hl2 = (df["High"] + df["Low"]) / 2
    atr_st = rma(tr, ST_ATR_PERIOD)
    upper, lower = hl2 + ST_FACTOR * atr_st, hl2 - ST_FACTOR * atr_st
    st, dir_st = upper.copy(), pd.Series(1, index=df.index)
    for i in range(1, len(df)):
        if df["Close"].iloc[i] > st.iloc[i-1]: dir_st.iloc[i] = -1
        elif df["Close"].iloc[i] < st.iloc[i-1]: dir_st.iloc[i] = 1
        else: dir_st.iloc[i] = dir_st.iloc[i-1]
        st.iloc[i] = lower.iloc[i] if dir_st.iloc[i] == -1 else upper.iloc[i]
    df["stDirection"] = dir_st

    df['di_cross_up'] = crossover(df["diplus"], df["diminus"])
    df['di_cross_down'] = crossunder(df["diplus"], df["diminus"])
    df['kijun_cross_down'] = crossunder(df["Close"], df["kijun"])

    df['main_cond'] = df['di_cross_up'] & df['obvUp'] & (df['Close'] > df['ma20']) & (df['Close'] > df['cloudTop'])
    df['st_buy_cond'] = (df["stDirection"] < 0) & (df["stDirection"].shift(1) > 0)
    df['st_sell_cond'] = (df["stDirection"] > 0) & (df["stDirection"].shift(1) < 0)
    df['sell_trigger_half'] = df['di_cross_down'] | df['kijun_cross_down']
    df['sell_trigger_half_once'] = df['sell_trigger_half'] & ~df['sell_trigger_half'].shift(1).fillna(False)

    trade_active = False
    sell_step = 0
    buy_bar_index = -1
    buy_price = np.nan
    fake_buy_block_price = np.nan
    fake_buy_block_pct = 5.0

    sig_main_buy, sig_st_buy = [False]*len(df), [False]*len(df)
    sig_half_sell, sig_full_sell = [False]*len(df), [False]*len(df)

    for i in range(len(df)):
        close_val = df["Close"].iloc[i]
        
        in_fake_zone = False
        if not pd.isna(fake_buy_block_price):
            upper_bound = fake_buy_block_price * (1 + fake_buy_block_pct / 100)
            lower_bound = fake_buy_block_price * (1 - fake_buy_block_pct / 100)
            if lower_bound <= close_val <= upper_bound:
                in_fake_zone = True

        actual_main_buy = df['main_cond'].iloc[i] and not in_fake_zone
        trend_buy = df['st_buy_cond'].iloc[i]
        buy_signal = (actual_main_buy or trend_buy) and not trade_active

        if buy_signal:
            trade_active = True
            sell_step = 0
            buy_bar_index = i
            buy_price = close_val
            
            if actual_main_buy:
                fake_buy_block_price = np.nan
                sig_main_buy[i] = True
            else: sig_st_buy[i] = True
            continue 

        sell_half_once = df['sell_trigger_half_once'].iloc[i]
        bars_after_buy = (i - buy_bar_index) if buy_bar_index != -1 else 0
        fast_half_sell = trade_active and (0 < bars_after_buy <= 3) and sell_half_once

        if trade_active and df['st_sell_cond'].iloc[i]:
            sig_full_sell[i] = True
            sell_step = 3
            trade_active = False
            fake_buy_block_price = buy_price
        elif trade_active and sell_step <= 1 and (fast_half_sell or sell_half_once):
            sig_half_sell[i] = True
            sell_step = 2
            fake_buy_block_price = buy_price

    df["SIGNAL_MAIN_BUY"] = sig_main_buy
    df["SIGNAL_ST_BUY"] = sig_st_buy
    df["SIGNAL_HALF_SELL"] = sig_half_sell
    df["SIGNAL_FULL_SELL"] = sig_full_sell
    return df

# --- 종목 스캔 파트 ---
def get_kr_tickers(top_n=400):
    for offset in range(0, 4):
        date = (datetime.now() - timedelta(days=offset)).strftime("%Y%m%d")
        res = {}
        try:
            df = stock.get_market_cap_by_ticker(date, market="KOSPI")
            if not df.empty:
                for m, s in [("KOSPI", ".KS"), ("KOSDAQ", ".KQ")]:
                    sub_df = stock.get_market_cap_by_ticker(date, market=m).sort_values("거래대금", ascending=False).head(top_n//2)
                    for c in sub_df.index: res[f"{c}{s}"] = stock.get_market_ticker_name(c)
                return res
        except: pass
    return {}

# 티커 캐싱 적용 (차단 방지)
def get_us_tickers(top_n=600):
    cache_file = "us_tickers_cache.json"
    cache_expiry = 86400 * 7  # 7일
    if os.path.exists(cache_file):
        if (time.time() - os.path.getmtime(cache_file)) < cache_expiry:
            with open(cache_file, "r") as f: return json.load(f)
    try:
        sp500 = pd.read_html("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies")[0]
        nasdaq100 = pd.read_html("https://en.wikipedia.org/wiki/Nasdaq-100")[4]
        combined = list(set(sp500["Symbol"].str.replace(".", "-", regex=False).tolist() + nasdaq100["Ticker"].tolist()))[:top_n]
        res = {s: s for s in combined}
        with open(cache_file, "w") as f: json.dump(res, f)
        return res
    except:
        if os.path.exists(cache_file):
            with open(cache_file, "r") as f: return json.load(f)
        return {}

if __name__ == "__main__":
    m_status = get_market_status()
    
    if MARKET_MODE == "US_OPTION":
        sig = get_high_conf_us_option_signal()
        if sig: send_discord(f"🇺🇸 **미국 옵션 실시간 모니터링**\n━━━━━━━━━━━━━━━━━━\n{sig}\n{m_status}\n━━━━━━━━━━━━━━━━━━")
        else: send_discord(f"✅ 특이사항 없음\n━━━━━━━━━━━━━━━━━━\n{m_status}")
    else:
        target = {}
        if MARKET_MODE in ["KR", "ALL"]: target.update(get_kr_tickers(KR_TOP_N))
        if MARKET_MODE in ["US", "ALL"]: target.update(get_us_tickers(US_TOP_N))
        
        found = []
        tickers_list = list(target.keys())
        
        # 벌크 다운로드 (속도 10배 이상 향상)
        if tickers_list:
            bulk_df = yf.download(tickers_list, period=PERIOD, interval=INTERVAL, group_by='ticker', progress=False)
            
            for t, name in target.items():
                try:
                    df = bulk_df[t].dropna() if len(tickers_list) > 1 else bulk_df.copy().dropna()
                    if df.empty or len(df) < 10: continue
                    
                    df = calculate_signals(df)
                    last_price = df.iloc[-1]["Close"]
                    s_type, detected_days_ago = None, 0
                    
                    for i in range(1, 8):
                        row = df.iloc[-i]
                        days_ago = i - 1  
                        if row["SIGNAL_MAIN_BUY"]: s_type = "MAIN BUY"
                        elif row["SIGNAL_ST_BUY"]: s_type = "ST BUY"
                        elif row["SIGNAL_HALF_SELL"]: s_type = "1/2 HALF SELL"
                        elif row["SIGNAL_FULL_SELL"]: s_type = "ST FULL SELL"
                        
                        if s_type:
                            detected_days_ago = days_ago
                            break
                    
                    if s_type: 
                        found.append({"t": t, "n": name, "s": s_type, "p": last_price, "d": detected_days_ago})
                except: continue
            
        if found:
            msg = f"🚨 [{MARKET_MODE}] 스캔 결과\n{m_status}\n"
            for s in found:
                news_txt = "\n".join([f"• {n}" for n in get_news_titles(s['n'], s['t'])])
                day_text = "오늘" if s['d'] == 0 else f"{s['d']}영업일 전"
                msg += f"\n[{s['s']}] {s['n']} ({s['t']})\n💰 현재가: {float(s['p']):.2f}\n⏳ 신호발생: {day_text}\n{news_txt}\n"
            send_discord(msg)
        else: 
            send_discord(f"✅ [{MARKET_MODE}] 시장 스캔 완료\n{m_status}\n현재 특이 신호 종목 없음")
