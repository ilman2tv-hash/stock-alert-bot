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
# [수정] timezone을 추가로 import 합니다.
from datetime import datetime, timedelta, timezone

# ==========================================
# 1. 환경 설정 및 상수
# ==========================================
WEBHOOK_URL = os.getenv("WEBHOOK_URL") 
MARKET_MODE = os.getenv("MARKET_MODE", "US_OPTION") # "KR", "US", "ALL", "US_OPTION"

translator = Translator()

PERIOD = "1y"
INTERVAL = "1d"

KR_TOP_N = 400  # 코스피 200개 + 코스닥 200개
US_TOP_N = 600  # S&P 500 + 나스닥 100
WATCHLIST = [
    "SMR", "OKLO", "RKLB", "RDW", "ASTS", "CRWV",
    "NBIS", "IREN", "AAOI", "COHR", "HIMS", "LUNR", "NTRA"
]

# 슈퍼트렌드 설정
ST_ATR_PERIOD = 10
ST_FACTOR = 3.0

# ==========================================
# 2. 디스코드 및 뉴스 유틸리티
# ==========================================
def send_discord(message):
    if not WEBHOOK_URL or "http" not in WEBHOOK_URL:
        print("Webhook URL이 설정되지 않았습니다.")
        return
    try:
        chunks = [message[i:i + 1900] for i in range(0, len(message), 1900)]
        for chunk in chunks:
            requests.post(WEBHOOK_URL, json={"content": chunk}, timeout=15)
    except Exception as e:
        print(f"Discord 전송 실패: {e}")

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
                try: 
                    t = translator.translate(t, dest="ko").text
                except Exception as e:
                    print(f"번역 오류 ({ticker}): {e}")
            titles.append(t)
        return titles if titles else ["관련 뉴스 없음"]
    except Exception as e:
        print(f"뉴스 검색 오류 ({ticker}): {e}")
        return ["뉴스 검색 오류"]

# ==========================================
# 3. 시장 상황 대시보드
# ==========================================
def get_market_status():
    try:
        data = yf.download(["SPY", "QQQ", "^VIX"], period="3mo", interval="1d", progress=False)
        if data.empty: return "📊 시장상황: 조회 실패"
        close = data["Close"].ffill().dropna()
        spy, qqq, vix = close["SPY"], close["QQQ"], close["^VIX"]
        
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
    except Exception as e:
        print(f"시장 상황 조회 오류: {e}")
        return "📊 시장상황: 데이터 부족"

# ==========================================
# 4. 기관 옵션 매집(Smart Money) 감지 
# ==========================================
def get_high_conf_us_option_signal():
    try:
        data = yf.download(["SPY", "^VIX", "^VVIX", "^SKEW"], period="3mo", interval="1d", progress=False)
        if data.empty:
            return "⚠️ 전체시장 주요 옵션 지수 데이터 부족"

        close = data["Close"].ffill() 
        spy = close["SPY"].dropna()
        vix = close["^VIX"].dropna()
        vvix = close["^VVIX"].dropna()
        skew = close["^SKEW"].dropna()

        curr_spy = float(spy.iloc[-1])
        curr_vix = float(vix.iloc[-1])
        curr_vvix = float(vvix.iloc[-1])
        curr_skew = float(skew.iloc[-1])
        
        prev_spy = float(spy.iloc[-2])
        prev_vvix = float(vvix.iloc[-2])
        
        spy_ma20 = spy.rolling(window=20).mean().iloc[-1]
        spy_change_pct = (curr_spy - prev_spy) / prev_spy * 100
        vvix_change_pct = (curr_vvix - prev_vvix) / prev_vvix * 100

        curr_pccr = None 
        is_market_closed = False

        try:
            spy_tk = yf.Ticker("SPY")
            if len(spy_tk.options) > 0:
                exp = spy_tk.options[0]
                chain = spy_tk.option_chain(exp)
                call_vol = chain.calls["volume"].fillna(0).sum()
                put_vol = chain.puts["volume"].fillna(0).sum()
                
                if call_vol == 0 and put_vol == 0:
                    is_market_closed = True
                else:
                    curr_pccr = put_vol / max(call_vol, 1)
            else:
                is_market_closed = True
        except Exception as opt_e:
            print(f"SPY 옵션 체인 파싱 실패: {opt_e}")

        signals = [f"📊 **[전체시장 옵션 현황]**"]
        
        if is_market_closed or curr_pccr is None:
            signals.append("• 시장 풋/콜 비율 (SPY PCR): ⏳ 미국 본장 개장 전 (데이터 없음)")
            signals.append(f"• 블랙스완 헤지 지수 (SKEW): {curr_skew:.2f}")
            signals.append(f"• 변동성 지수 (VIX): {curr_vix:.2f} / (VVIX): {curr_vvix:.2f}")
            signals.append(f"━━━━━━━━━━━━━━━━━━")
            signals.append(f"🔎 **[특이 신호 감지 결과]**")
            signals.append(f"💤 현재 미국 프리마켓 시간대이므로 실시간 옵션 체인이 활성화되지 않았습니다. 본장(오후 10:30 이후)에 데이터를 확인하세요.")
            return "\n".join(signals)

        signals.append(f"• 시장 풋/콜 비율 (SPY PCR): {curr_pccr:.2f}")
        signals.append(f"• 블랙스완 헤지 지수 (SKEW): {curr_skew:.2f}")
        signals.append(f"• 변동성 지수 (VIX): {curr_vix:.2f} / (VVIX): {curr_vvix:.2f}")
        signals.append(f"━━━━━━━━━━━━━━━━━━")
        signals.append(f"🔎 **[특이 신호 감지 결과]**")

        has_alert = False

        if curr_skew >= 135:
            has_alert = True
            signals.append(f"🚨 **[블랙스완 경고]** 기관들이 대폭락(Tail Risk) 풋옵션 보험을 대거 체결했습니다!\n▶ SKEW 위험수위 돌파: {curr_skew:.2f}")

        if curr_vvix > 105 and vvix_change_pct > 5.0 and spy_change_pct >= -0.2:
            has_alert = True
            signals.append(f"⚠️ **[VIX 선행 급등]** 주가는 방어 중이나 내부 변동성(VVIX)이 치솟고 있습니다.\n▶ VVIX 스파이크: {curr_vvix:.2f} (전일대비 +{vvix_change_pct:.1f}%)")

        if curr_pccr > 1.3:
            has_alert = True
            signals.append(f"🩸 **[투매 절정/역발상]** 시장 전체의 풋옵션 패닉 바잉이 극단적 수준입니다.\n▶ 극단적 공포 상태 (PCR: {curr_pccr:.2f}). 강력한 숏커버링 반등 가능성 존재.")
        elif curr_pccr > 0.95 and spy_change_pct > -0.5:
            has_alert = True
            signals.append(f"🛑 **[기만적 풋 매집]** 지수는 버티는데 당일 시장 풋옵션 거래량 비중이 비정상 폭증했습니다.\n▶ 시장 PCR: {curr_pccr:.2f} / 당일 주가방어율: {spy_change_pct:.2f}%")
        elif curr_pccr < 0.5:
            has_alert = True
            signals.append(f"🚀 **[상승 전환 전조]** 지수 흐름 대비 스마트머니의 강력한 콜옵션 매집이 우세합니다.\n▶ 시장 PCR (콜 우위): {curr_pccr:.2f}")

        if curr_spy < spy_ma20 and curr_vvix < 90 and vvix_change_pct < -5.0:
            has_alert = True
            signals.append(f"🟢 **[변동성 압착]** 시장은 아직 약세장이나 VVIX가 선행하여 급락 안정화 중입니다.\n▶ 하방 압력이 해소되고 반등 랠리가 나올 확률이 높습니다.")

        if not has_alert:
            if curr_spy < spy_ma20 and curr_vix > 20:
                signals.append(f"🧊 **[하락 추세 진행 중]** 전체적인 시장 리스크가 잔존하므로 보수적인 포지션을 권장합니다. (SPY 20일선 하회)")
            else:
                signals.append(f"✅ 현재 전체시장 옵션 지표에서 특이 변동성 폭발이나 급격한 쏠림 징후가 없는 무난한 상태입니다.")

        return "\n".join(signals)

    except Exception as e:
        print(f"옵션 스캔 오류: {e}")
        return f"⚠️ 전체시장 옵션 스캔 중 오류 발생: {e}"

def get_watchlist_option_signals():
    result = []
    for ticker in WATCHLIST:
        try:
            tk = yf.Ticker(ticker)
            if len(tk.options) == 0:
                continue

            exp = tk.options[0]
            chain = tk.option_chain(exp)

            call_vol = chain.calls["volume"].fillna(0).sum()
            put_vol = chain.puts["volume"].fillna(0).sum()

            if call_vol == 0 and put_vol == 0:
                continue

            call_oi = chain.calls["openInterest"].fillna(0).sum()
            put_oi = chain.puts["openInterest"].fillna(0).sum()

            if call_vol < 100:
                continue

            pcr = put_vol / max(call_vol, 1)

            if pcr > 1.5:
                result.append(f"🔴 {ticker} 강한 풋매집\nP/C={pcr:.2f}")
            elif pcr > 1.2:
                result.append(f"🚨 {ticker} 선행위험\nP/C={pcr:.2f}")
            elif pcr > 1.0:
                result.append(f"🛑 {ticker} 기만적 풋매집 가능\nP/C={pcr:.2f}")
            elif pcr < 0.5 and call_oi > put_oi:
                result.append(f"🚀 {ticker} 콜매집\nP/C={pcr:.2f}")
            
            time.sleep(0.5) 

        except Exception as e:
            print(f"Watchlist 조회 오류 ({ticker}): {e}")
            continue

    return result

# ==========================================
# 5. 메인 지표 계산 (PineScript 로직 완벽 이식)
# ==========================================
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
    st, dir_st = pd.Series(upper, index=df.index), pd.Series(1, index=df.index)
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

# ==========================================
# 6. 티커 스크랩 함수
# ==========================================
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
        except Exception as e:
            print(f"KR Ticker 스크랩 오류: {e}")
            pass
    return {}

def get_us_tickers(top_n=600):
    cache_file = "us_tickers_cache.json"
    cache_expiry = 86400 * 7  
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
    except Exception as e:
        print(f"US Ticker 스크랩 오류: {e}")
        if os.path.exists(cache_file):
            with open(cache_file, "r") as f: return json.load(f)
        return {}

# ==========================================
# 7. 메인 실행 (Cron 1회 실행용 단일 구조)
# ==========================================
if __name__ == "__main__":
    # [수정] 한국 시간대(KST = UTC+9)를 생성하고 적용하여 구동합니다.
    kst = timezone(timedelta(hours=9))
    current_time_str = datetime.now(kst).strftime("%H:%M")
    
    m_status = get_market_status()
    
    if MARKET_MODE == "US_OPTION":
        market_sig = get_high_conf_us_option_signal()
        stock_sig = get_watchlist_option_signals()

        msg = f"🇺🇸 미국 옵션 실시간 모니터링 ({current_time_str} 실행)\n"
        msg += "━━━━━━━━━━━━━━━━━━\n"

        if market_sig:
            msg += market_sig + "\n\n"

        if stock_sig:
            msg += "📈 관심종목 옵션감시\n"
            msg += "\n\n".join(stock_sig)
            msg += "\n\n"

        msg += m_status
        msg += "\n━━━━━━━━━━━━━━━━━━"

        send_discord(msg)
        
    else:
        target = {}
        if MARKET_MODE in ["KR", "ALL"]: target.update(get_kr_tickers(KR_TOP_N))
        if MARKET_MODE in ["US", "ALL"]: target.update(get_us_tickers(US_TOP_N))
        
        found = []
        tickers_list = list(target.keys())
        
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
                except Exception as e: 
                    continue
            
        if found:
            msg = f"🚨 [{MARKET_MODE}] 스캔 결과 ({current_time_str})\n{m_status}\n"
            for s in found:
                news_txt = "\n".join([f"• {n}" for n in get_news_titles(s['n'], s['t'])])
                day_text = "오늘" if s['d'] == 0 else f"{s['d']}영업일 전"
                msg += f"\n[{s['s']}] {s['n']} ({s['t']})\n💰 현재가: {float(s['p']):.2f}\n⏳ 신호발생: {day_text}\n{news_txt}\n"
            send_discord(msg)
        else: 
            send_discord(f"✅ [{MARKET_MODE}] 시장 스캔 완료 ({current_time_str})\n{m_status}\n현재 특이 신호 종목 없음")
