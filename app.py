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
        with st.spinner("正在獲取 SqueezeMetrics 大盤暗池與 GEX 數據..."):
            sm_df = fetch_squeezemetrics_data()
            if sm_df is not None and not sm_df.empty:
                latest = sm_df.iloc[-1]
                prev = sm_df.iloc[-2]
                
                st.markdown("## 🌐 標普 500 大盤總體環境 (SqueezeMetrics)")
                st.caption(f"📅 官方數據更新日期: {latest['date'].strftime('%Y-%m-%d')} (通常為前一交易日收盤後)")
                
                # 計算數值
                sm_gex_latest = latest['gex'] / 1e9  # 十億 (B)
                sm_gex_prev = prev['gex'] / 1e9
                sm_dix_latest = latest['dix'] * 100
                sm_dix_prev = prev['dix'] * 100
                
                col_sm1, col_sm2, col_sm3 = st.columns(3)
                
                # GEX 指標
                gex_status = "🟢 穩定護盤期" if sm_gex_latest > 0 else "🔴 高波動狂暴期"
                col_sm1.metric("SPX 官方總體 GEX", f"{sm_gex_latest:.2f} B", f"{sm_gex_latest - sm_gex_prev:.2f} B", delta_color="normal" if sm_gex_latest > 0 else "inverse")
                
                # DIX 指標
                if sm_dix_latest >= 45.0: dix_status = "🔥 極度貪婪 (法人接刀)"
                elif sm_dix_latest <= 35.0: dix_status = "❄️ 極度冷清 (法人離席)"
                else: dix_status = "⚪ 中性水準"
                col_sm2.metric(f"暗池指數 (DIX) - {dix_status}", f"{sm_dix_latest:.1f}%", f"{sm_dix_latest - sm_dix_prev:.1f}%")
                
                # 策略判定
                if sm_gex_latest < 0 and sm_dix_latest >= 45.0:
                    col_sm3.error("**大盤策略**: 🎯 狙擊期 (2倍做多)\n\n(恐慌殺盤中法人爆買，準備 V 轉)")
                elif sm_gex_latest > 0:
                    col_sm3.success("**大盤策略**: 🛡️ 平穩期 (1倍/2倍做多)\n\n(莊家護盤中，拉回找買點)")
                else:
                    col_sm3.warning("**大盤策略**: 🌪️ 風暴期 (空手抱現金)\n\n(負伽馬且法人未接刀，極度危險)")
            else:
                st.warning("無法取得 SqueezeMetrics 數據，請稍後再試。")
                
        # --- 6.2 個股迴圈掃描 ---
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
                        calls['impliedVolatility'] = calls['impliedVolatility'].fillna(0.01)
                        puts['impliedVolatility'] = puts['impliedVolatility'].fillna(0.01)
                        
                        total_call_oi += calls['openInterest'].sum()
                        total_put_oi += puts['openInterest'].sum()
                        
                        for _, row in calls.iterrows():
                            if row['openInterest'] == 0: continue
                            gamma = calc_gamma(spot_price, row['strike'], T, risk_free_rate, row['impliedVolatility'])
                            gex = row['openInterest'] * gamma * 100 * spot_price * 0.01
                            gex_data.append({'Strike': row['strike'], 'GEX': gex, 'Type': 'Call', 'Bucket': bucket})
                            
                        for _, row in puts.iterrows():
                            if row['openInterest'] == 0: continue
                            gamma = calc_gamma(spot_price, row['strike'], T, risk_free_rate, row['impliedVolatility'])
                            gex = -row['openInterest'] * gamma * 100 * spot_price * 0.01
                            gex_data.append({'Strike': row['strike'], 'GEX': gex, 'Type': 'Put', 'Bucket': bucket})
                            
                    if not gex_data:
                        st.warning(f"{ticker} 範圍內無有效的 GEX 數據。")
                        continue
                    
                    total_oi = total_call_oi + total_put_oi
                    pcr = total_put_oi / total_call_oi if total_call_oi > 0 else 0
                    
                    df = pd.DataFrame(gex_data)
                    df_grouped = df.groupby(['Strike', 'Type', 'Bucket'])['GEX'].sum().reset_index()
                    
                    range_pct = range_input / 100.0
                    lower_bound, upper_bound = spot_price * (1 - range_pct), spot_price * (1 + range_pct)
                    df_filtered = df_grouped[(df_grouped['Strike'] >= lower_bound) & (df_grouped['Strike'] <= upper_bound)]
                    
                    df_total_by_strike = df_filtered.groupby('Strike')['GEX'].sum().reset_index().sort_values(by='Strike')
                    total_gex = df_total_by_strike['GEX'].sum() / 1e6
                    
                    max_call_wall = 0
                    if not df_total_by_strike[df_total_by_strike['GEX'] > 0].empty:
                        max_call_wall = df_total_by_strike[df_total_by_strike['GEX'] > 0].loc[df_total_by_strike['GEX'].idxmax()]['Strike']
                        
                    max_put_wall = 0
                    if not df_total_by_strike[df_total_by_strike['GEX'] < 0].empty:
                        max_put_wall = df_total_by_strike[df_total_by_strike['GEX'] < 0].loc[df_total_by_strike['GEX'].idxmin()]['Strike']
                    
                    zero_gamma_level = 0
                    closest_distance = float('inf')
                    for i in range(len(df_total_by_strike) - 1):
                        gex_1, gex_2 = df_total_by_strike.iloc[i]['GEX'], df_total_by_strike.iloc[i+1]['GEX']
                        if (gex_1 < 0 and gex_2 > 0) or (gex_1 > 0 and gex_2 < 0):
                            avg_strike = (df_total_by_strike.iloc[i]['Strike'] + df_total_by_strike.iloc[i+1]['Strike']) / 2
                            dist = abs(avg_strike - spot_price)
                            if dist < closest_distance:
                                closest_distance, zero_gamma_level = dist, avg_strike
                    
                    zg_display = f"${zero_gamma_level:.2f}" if zero_gamma_level > 0 else "無明顯交界"

                    # --- 雲端寫入 Google Sheets 邏輯 ---
                    new_data = {
                        "Date": today_date_str,
                        "Spot Price": round(spot_price, 2),
                        "Total GEX (M)": round(total_gex, 2),
                        "P/C Ratio": round(pcr, 2),
                        "Zero Gamma": round(zero_gamma_level, 2),
                        "Call Wall": max_call_wall,
                        "Put Wall": max_put_wall
                    }
                    history_df = pd.DataFrame([new_data])

                    if gs_client:
                        try:
                            sheet = gs_client.open("GEX_History")
                            try:
                                worksheet = sheet.worksheet(ticker)
                            except gspread.WorksheetNotFound:
                                worksheet = sheet.add_worksheet(title=ticker, rows="1000", cols="10")
                                worksheet.append_row(list(new_data.keys()))
                            
                            records = worksheet.get_all_records()
                            if records:
                                history_df = pd.DataFrame(records)
                                history_df['Date'] = history_df['Date'].astype(str)
                                
                                if today_date_str in history_df['Date'].values:
                                    history_df.loc[history_df['Date'] == today_date_str, list(new_data.keys())] = list(new_data.values())
                                else:
                                    history_df = pd.concat([history_df, pd.DataFrame([new_data])], ignore_index=True)
                            
                            worksheet.clear()
                            worksheet.update([history_df.columns.values.tolist()] + history_df.values.tolist())
                            st.toast(f'✅ {ticker} 歷史數據已同步至 Google 雲端！', icon='☁️')
                            
                        except Exception as e:
                            st.error(f"寫入 Google 試算表時發生錯誤: {e}")

                    # --- AI 策略雷達 & 流動性防呆機制 ---
                    alerts = []
                    threshold = 0.015
                    
                    if total_oi < 50000:
                        alerts.append(("🛑 嚴重警告：期權流動性不足", f"此標的期權總未平倉量僅 **{int(total_oi):,} 口**。流動性過低，GEX 支撐壓力無效，請改用技術面分析！", "error"))
                    else:
                        if is_near_opex(today):
                            alerts.append(("📅 策略五：OpEx 結算日變盤警告", "目前正值選擇權大結算前後！大量 Gamma 即將蒸發，請密切留意結算後是否出現單邊突破行情。", "warning"))
                        if zero_gamma_level > 0 and abs(spot_price - zero_gamma_level) / spot_price <= threshold:
                            alerts.append(("⚡ 策略四：Zero Gamma 多空決戰點", f"當前股價處於多空分水嶺 (${zero_gamma_level:.2f}) 邊緣！若帶量跌破適合做空，強勢站穩適合做多。", "info"))
                        if max_put_wall > 0 and abs(spot_price - max_put_wall) / spot_price <= threshold:
                            alerts.append(("🔥 策略二：Put Wall 極限支撐", f"股價極度逼近最大下檔支撐牆 (${max_put_wall})！這是一個極佳的反轉買點。", "success"))
                        if max_call_wall > 0 and abs(spot_price - max_call_wall) / spot_price <= threshold:
                            alerts.append(("⚠️ 策略三：Call Wall 泰山壓頂", f"股價極度逼近最大上檔壓力牆 (${max_call_wall})！多單請考慮獲利了結或尋找放空時機。", "warning"))
                        if total_gex > 0 and not (abs(spot_price - max_put_wall) / spot_price <= threshold) and not (abs(spot_price - max_call_wall) / spot_price <= threshold):
                            alerts.append(("💡 策略一：正 GEX 區間震盪", f"目前整體市場 GEX 為正 ({total_gex:.2f} M)，最佳策略是高拋低吸。", "success"))
                        elif total_gex < 0 and not (abs(spot_price - zero_gamma_level) / spot_price <= threshold):
                            alerts.append(("🚨 警告：負 GEX 狂暴模式", f"目前整體市場 GEX 為負 ({total_gex:.2f} M)！市場極度脆弱，切勿隨便摸底。", "error"))

                    if alerts:
                        st.markdown("### 🤖 策略雷達：自動進出場偵測")
                        for title, desc, atype in alerts:
                            if atype == "success": st.success(f"**{title}**\n\n{desc}")
                            elif atype == "warning": st.warning(f"**{title}**\n\n{desc}")
                            elif atype == "error": st.error(f"**{title}**\n\n{desc}")
                            elif atype == "info": st.info(f"**{title}**\n\n{desc}")
                    
                    st.markdown("---")
                    
                    col1, col2, col3 = st.columns(3)
                    col1.metric("當前股價", f"${spot_price:.2f}")
                    gex_status = "🟢 正伽馬" if total_gex > 0 else "🔴 負伽馬"
                    col2.metric("總體 GEX", f"{total_gex:.2f} M", gex_status, delta_color="normal" if total_gex > 0 else "inverse")
                    col3.metric("P/C Ratio", f"{pcr:.2f}")
                    
                    col4, col5, col6 = st.columns(3)
                    col4.metric("Zero Gamma", zg_display)
                    col5.metric("Call Wall", f"${max_call_wall}")
                    col6.metric("Put Wall", f"${max_put_wall}")
                    
                    tab1, tab2 = st.tabs(["📈 GEX 歷史趨勢與資料庫", "🧱 GEX 期限結構分佈圖"])
                    
                    with tab1:
                        if len(history_df) > 1:
                            chart_data = history_df.set_index("Date")[["Total GEX (M)"]]
                            st.line_chart(chart_data)
                        else:
                            st.info("📌 這是您第一天記錄資料！請明天再次點擊計算，這裡就會自動畫出歷史趨勢折線圖。")
                        st.dataframe(history_df, use_container_width=True)

                    with tab2:
                        fig, ax = plt.subplots(figsize=(12, 6))
                        buckets_order = ['0-7 Days', '8-30 Days', '31-90 Days', '>90 Days']
                        call_colors = {'0-7 Days': '#98FB98', '8-30 Days': '#3CB371', '31-90 Days': '#2E8B57', '>90 Days': '#006400'}
                        put_colors = {'0-7 Days': '#FFB6C1', '8-30 Days': '#FF6347', '31-90 Days': '#DC143C', '>90 Days': '#8B0000'}
                        
                        unique_strikes = sorted(df_filtered['Strike'].unique())
                        pos_bottoms = np.zeros(len(unique_strikes))
                        neg_bottoms = np.zeros(len(unique_strikes))
                        strike_idx_map = {strike: i for i, strike in enumerate(unique_strikes)}
                        
                        for bucket in buckets_order:
                            call_data = df_filtered[(df_filtered['Type'] == 'Call') & (df_filtered['Bucket'] == bucket)]
                            if not call_data.empty:
                                values = np.zeros(len(unique_strikes))
                                for _, row in call_data.iterrows(): values[strike_idx_map[row['Strike']]] = row['GEX'] / 1e6
                                ax.bar(unique_strikes, values, bottom=pos_bottoms, width=1, color=call_colors[bucket], label=f'Call: {bucket}', alpha=0.9)
                                pos_bottoms += values
                                
                            put_data = df_filtered[(df_filtered['Type'] == 'Put') & (df_filtered['Bucket'] == bucket)]
                            if not put_data.empty:
                                values = np.zeros(len(unique_strikes))
                                for _, row in put_data.iterrows(): values[strike_idx_map[row['Strike']]] = row['GEX'] / 1e6
                                ax.bar(unique_strikes, values, bottom=neg_bottoms, width=1, color=put_colors[bucket], label=f'Put: {bucket}', alpha=0.9)
                                neg_bottoms += values

                        ax.axvline(x=spot_price, color='blue', linestyle='-', linewidth=2, label=f"Spot Price: {spot_price:.2f}")
                        if zero_gamma_level > 0:
                            ax.axvline(x=zero_gamma_level, color='orange', linestyle='--', linewidth=2, label=f"Zero Gamma: {zero_gamma_level:.2f}")
                        
                        ax.set_xlabel("Strike Price")
                        ax.set_ylabel("Dollar GEX (Millions)")
                        ax.grid(True, alpha=0.3)
                        handles, labels = ax.get_legend_handles_labels()
                        by_label = dict(zip(labels, handles))
                        ax.legend(by_label.values(), by_label.keys(), loc='upper left', bbox_to_anchor=(1, 1))
                        plt.tight_layout()
                        st.pyplot(fig)
                        
                except Exception as e:
                    st.error(f"計算 {ticker} 時發生錯誤: {e}")

