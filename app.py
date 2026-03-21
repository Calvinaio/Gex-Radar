import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
from scipy.stats import norm
import matplotlib.pyplot as plt
import datetime
import calendar
import json
import gspread
from google.oauth2.service_account import Credentials

# --- 1. Google Sheets 連線設定 ---
@st.cache_resource
def get_gspread_client():
    try:
        creds_json = st.secrets["GOOGLE_CREDENTIALS"]
        creds_dict = json.loads(creds_json)
        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive"
        ]
        creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
        client = gspread.authorize(creds)
        return client
    except Exception as e:
        st.error(f"Google 金鑰讀取失敗，請檢查 Secrets 設定。錯誤: {e}")
        return None

# --- 2. SqueezeMetrics 數據抓取 ---
@st.cache_data(ttl=3600)
def fetch_squeezemetrics_data():
    try:
        url = "https://squeezemetrics.com/monitor/static/DIX.csv"
        df = pd.read_csv(url)
        df['date'] = pd.to_datetime(df['date'])
        df = df.sort_values('date').reset_index(drop=True)
        return df
    except Exception as e:
        return None

# --- 3. 核心運算函數 ---
def calc_gamma(S, K, T, r, sigma):
    if T <= 0 or sigma <= 0.01:
        return 0.0
    d1 = (np.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * np.sqrt(T))
    gamma = norm.pdf(d1) / (S * sigma * np.sqrt(T))
    return gamma

def get_dte_bucket(days):
    if days <= 7: return '0-7 Days'
    elif days <= 30: return '8-30 Days'
    elif days <= 90: return '31-90 Days'
    else: return '>90 Days'

def is_near_opex(date_obj):
    c = calendar.Calendar(firstweekday=calendar.SUNDAY)
    monthcal = c.monthdatescalendar(date_obj.year, date_obj.month)
    fridays = [d for week in monthcal for d in week if d.weekday() == calendar.FRIDAY and d.month == date_obj.month]
    if len(fridays) >= 3:
        third_friday = fridays[2]
        diff = (date_obj.date() - third_friday).days
        if -3 <= diff <= 2: 
            return True
    return False

# --- 4. 網頁介面設定 ---
st.set_page_config(page_title="GEX 專業分析儀表板", layout="wide")
st.title("📈 終極版 GEX 雲端籌碼雷達")
st.markdown("結合 **SqueezeMetrics 大盤數據**、**個股策略提示** 與 **Google 試算表自動存檔**。")

# --- 5. 側邊欄與輸入區 ---
with st.sidebar:
    st.header("⚙️ 參數設定")
    ticker_input = st.text_input(
        "輸入股票代碼 (以逗號分隔)：", 
        "SPY, QQQ, IWM, DIA, SOXX, TSLA, NVDA, AAPL, MSFT, AMD, META, AMZN, GOOGL, AVGO, MU, TSM"
    )
    days_input = st.slider("分析未來幾天內到期的期權？", min_value=1, max_value=365, value=60)
    range_input = st.slider("履約價掃描範圍 (上下 %)", min_value=5, max_value=50, value=15, step=5)
    risk_free_rate = st.number_input("無風險利率設定 (%)", value=4.0) / 100.0
    run_button = st.button("🚀 開始掃描籌碼與策略", use_container_width=True)

# --- 6. 主程式邏輯 ---
if run_button:
    tickers = [t.strip().upper() for t in ticker_input.split(",")]
    
    if not tickers or tickers == [""]:
        st.warning("請輸入至少一個股票代碼！")
    else:
        today_date_str = datetime.datetime.now().strftime("%Y-%m-%d")
        gs_client = get_gspread_client()
        
        # --- 6.1 大盤總體環境 (SqueezeMetrics) ---
        sm_gex_ma5_latest = 0  # 預設值防呆
        with st.spinner("正在獲取 SqueezeMetrics 大盤暗池與 GEX 數據..."):
            sm_df = fetch_squeezemetrics_data()
            if sm_df is not None and not sm_df.empty:
                # 計算 GEX 的 5 日移動平均
                sm_df['gex_ma5'] = sm_df['gex'].rolling(window=5).mean()
                
                latest = sm_df.iloc[-1]
                prev = sm_df.iloc[-2]
                
                st.markdown("## 🌐 標普 500 大盤總體環境 (SqueezeMetrics)")
                st.caption(f"📅 官方數據更新日期: {latest['date'].strftime('%Y-%m-%d')} (通常為前一交易日收盤後)")
                
                # 計算數值 (除以 1e9 轉成十億 B 單位)
                sm_gex_latest = latest['gex'] / 1e9  
                sm_gex_prev = prev['gex'] / 1e9
                sm_gex_ma5_latest = latest['gex_ma5'] / 1e9  # 取得最新 5MA
                sm_dix_latest = latest['dix'] * 100
                sm_dix_prev = prev['dix'] * 100
                
                # 畫面切為 4 欄以容納 5MA 指標
                col_sm1, col_sm2, col_sm3, col_sm4 = st.columns(4)
                
                # GEX 指標
                gex_status = "🟢 穩定護盤期" if sm_gex_latest > 0 else "🔴 高波動狂暴期"
                col_sm1.metric("SPX 官方總體 GEX", f"{sm_gex_latest:.2f} B", f"{sm_gex_latest - sm_gex_prev:.2f} B", delta_color="normal" if sm_gex_latest > 0 else "inverse")
                
                # GEX 5MA 指標
                ma5_status = "📈 趨勢偏多" if sm_gex_ma5_latest > 0 else "📉 趨勢偏空"
                col_sm2.metric("GEX 5日均線 (趨勢)", f"{sm_gex_ma5_latest:.2f} B", ma5_status, delta_color="off")
                
                # DIX 指標
                if sm_dix_latest >= 45.0: dix_status = "🔥 極度貪婪 (法人接刀)"
                elif sm_dix_latest <= 35.0: dix_status = "❄️ 極度冷清 (法人離席)"
                else: dix_status = "⚪ 中性水準"
                col_sm3.metric(f"暗池指數 (DIX) - {dix_status}", f"{sm_dix_latest:.1f}%", f"{sm_dix_latest - sm_dix_prev:.1f}%")
                
                # 策略判定
                if sm_gex_latest < 0 and sm_dix_latest >= 45.0:
                    col_sm4.error("**大盤策略**: 🎯 狙擊期 (2倍做多)\n\n(恐慌殺盤中法人爆買，準備 V 轉)")
                elif sm_gex_latest > 0:
                    col_sm4.success("**大盤策略**: 🛡️ 平穩期 (1倍/2倍做多)\n\n(莊家護盤中，拉回找買點)")
                else:
                    col_sm4.warning("**大盤策略**: 🌪️ 風暴期 (空手抱現金)\n\n(負伽馬且法人未接刀，極度危險)")
            else:
                st.warning("無法取得 SqueezeMetrics 數據，請稍後再試。")
                
      # --- 6.2 個股迴圈掃描 ---
        summary_data = []  # 👈 1. 在迴圈開始前，初始化清單
        
        for ticker in tickers:
            st.markdown("---")
            st.subheader(f"🎯 {ticker} 個股籌碼觀測站")
            
            with st.spinner(f"正在掃描 {ticker} 的選擇權數據並計算..."):
                try:
                    stock = yf.Ticker(ticker)
                    hist = stock.history(period="1d")
                    if hist.empty:
                        st.error(f"找不到 {ticker} 的股價資料。")
                        continue
                    spot_price = hist['Close'].iloc[-1]
                    
                    expirations = stock.options
                    if not expirations:
                        st.warning(f"{ticker} 目前沒有可用的期權數據。")
                        continue
                        
                    today = datetime.datetime.now()
                    gex_data = []
                    total_call_oi, total_put_oi = 0, 0
                    
                    for date_str in expirations:
                        exp_date = datetime.datetime.strptime(date_str, "%Y-%m-%d")
                        days_to_exp = (exp_date - today).days
                        if days_to_exp < 0 or days_to_exp > days_input: continue
                            
                        T = (days_to_exp + 0.5) / 365.0
                        bucket = get_dte_bucket(days_to_exp)
                        opt = stock.option_chain(date_str)
                        
                        calls, puts = opt.calls.copy(), opt.puts.copy()
                        calls['openInterest'] = calls['openInterest'].fillna(0)
                        puts['openInterest'] = puts['openInterest'].fillna(0)
                        
                        total_call_oi += calls['openInterest'].sum()
                        total_put_oi += puts['openInterest'].sum()
                        
                        for _, row in calls.iterrows():
                            if row['openInterest'] == 0: continue
                            gamma = calc_gamma(spot_price, row['strike'], T, risk_free_rate, row.get('impliedVolatility', 0.2))
                            gex = row['openInterest'] * gamma * 100 * spot_price * 0.01
                            gex_data.append({'Strike': row['strike'], 'GEX': gex, 'Type': 'Call', 'Bucket': bucket})
                            
                        for _, row in puts.iterrows():
                            if row['openInterest'] == 0: continue
                            gamma = calc_gamma(spot_price, row['strike'], T, risk_free_rate, row.get('impliedVolatility', 0.2))
                            gex = -row['openInterest'] * gamma * 100 * spot_price * 0.01
                            gex_data.append({'Strike': row['strike'], 'GEX': gex, 'Type': 'Put', 'Bucket': bucket})
                            
                    if not gex_data:
                        st.warning(f"{ticker} 範圍內無有效的 GEX 數據。")
                        continue
                    
                    # --- 計算指標 ---
                    total_oi = total_call_oi + total_put_oi
                    pcr = total_put_oi / total_call_oi if total_call_oi > 0 else 0
                    df_temp = pd.DataFrame(gex_data)
                    df_total_by_strike = df_temp.groupby('Strike')['GEX'].sum().reset_index().sort_values(by='Strike')
                    total_gex = df_total_by_strike['GEX'].sum() / 1e6
                    
                    max_call_wall = df_total_by_strike[df_total_by_strike['GEX'] > 0].loc[df_total_by_strike[df_total_by_strike['GEX'] > 0]['GEX'].idxmax()]['Strike'] if not df_total_by_strike[df_total_by_strike['GEX'] > 0].empty else 0
                    max_put_wall = df_total_by_strike[df_total_by_strike['GEX'] < 0].loc[df_total_by_strike[df_total_by_strike['GEX'] < 0]['GEX'].idxmin()]['Strike'] if not df_total_by_strike[df_total_by_strike['GEX'] < 0].empty else 0
                    
                    zero_gamma_level = 0
                    for i in range(len(df_total_by_strike) - 1):
                        if (df_total_by_strike.iloc[i]['GEX'] < 0 and df_total_by_strike.iloc[i+1]['GEX'] > 0) or (df_total_by_strike.iloc[i]['GEX'] > 0 and df_total_by_strike.iloc[i+1]['GEX'] < 0):
                            zero_gamma_level = (df_total_by_strike.iloc[i]['Strike'] + df_total_by_strike.iloc[i+1]['Strike']) / 2
                            break
                    
                    # 👈 2. 【關鍵修正】在迴圈內，將當前 ticker 的結果存入 summary_data
                    dist_call = abs(spot_price - max_call_wall) / spot_price if max_call_wall > 0 else 9.9
                    dist_put = abs(spot_price - max_put_wall) / spot_price if max_put_wall > 0 else 9.9
                    dist_zero = abs(spot_price - zero_gamma_level) / spot_price if zero_gamma_level > 0 else 9.9
                    
                    summary_data.append({
                        "代號": ticker,
                        "股價": round(spot_price, 2),
                        "GEX 狀態": "🟢 正" if total_gex > 0 else "🔴 負",
                        "Total GEX(M)": round(total_gex, 2),
                        "靠近 Call Wall": "⚠️ 靠近" if dist_call <= 0.02 else "---",
                        "靠近 Put Wall": "🛡️ 靠近" if dist_put <= 0.02 else "---",
                        "靠近 Zero Gamma": "⚡ 決戰點" if dist_zero <= 0.02 else "---",
                        "P/C Ratio": round(pcr, 2)
                    })

                    # --- 接下來是原本的顯示邏輯 (與雲端寫入) ---
                    # [此處保留您原本的雲端寫入和各別股票的 alerts / 儀表板顯示代碼]
                    # ... (省略中間重複的顯示程式碼) ...
                    
                    # 確保您的 alert 邏輯中包含：
                    # if total_gex > 0 and dist_put <= 0.015: st.info("💰 策略：正 GEX 低波收租 (Short Put)...")

                except Exception as e:
                    st.error(f"計算 {ticker} 時發生錯誤: {e}")

        # 👈 3. 【關鍵修正】移到迴圈外！當所有股票跑完，才顯示總表
        st.markdown("---")
        st.header("📊 全市場籌碼狀態總覽 (Summary)")
        
        if summary_data:
            summary_df = pd.DataFrame(summary_data)
            
            # 設定顏色樣式
            def color_gex_status(val):
                color = '#28a745' if '🟢' in val else '#dc3545'
                return f'color: {color}; font-weight: bold'

            styled_summary = summary_df.style.applymap(color_gex_status, subset=['GEX 狀態'])
            st.dataframe(styled_summary, use_container_width=True)
            
            # 小統計
            pos_stocks = [d['代號'] for d in summary_data if "正" in d['GEX 狀態']]
            neg_stocks = [d['代號'] for d in summary_data if "負" in d['GEX 狀態']]
            st.success(f"📈 正 GEX 標的 ({len(pos_stocks)}): {', '.join(pos_stocks)}")
            st.error(f"📉 負 GEX 標的 ({len(neg_stocks)}): {', '.join(neg_stocks)}")
