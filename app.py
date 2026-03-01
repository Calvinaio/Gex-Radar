import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
from scipy.stats import norm
import matplotlib.pyplot as plt
import datetime
import os
import calendar

# --- 核心運算函數 ---
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
    """偵測是否靠近每個月第三個星期五的結算日 (OpEx)"""
    c = calendar.Calendar(firstweekday=calendar.SUNDAY)
    monthcal = c.monthdatescalendar(date_obj.year, date_obj.month)
    fridays = [d for week in monthcal for d in week if d.weekday() == calendar.FRIDAY and d.month == date_obj.month]
    if len(fridays) >= 3:
        third_friday = fridays[2]
        diff = (date_obj.date() - third_friday).days
        if -3 <= diff <= 2: 
            return True
    return False

# --- 網頁介面設定 ---
st.set_page_config(page_title="GEX 專業分析儀表板", layout="wide")
st.title("📈 終極版 GEX 籌碼雷達與策略提示")
st.markdown("結合 **期限結構**、**買賣權比 (P/C Ratio)**、**AI 自動策略偵測** 與 **流動性防呆機制**。")

# --- 側邊欄與輸入區 ---
with st.sidebar:
    st.header("⚙️ 參數設定")
    # 🌟 更新預設標的為最適合 GEX 的大權值與指數
    ticker_input = st.text_input("輸入股票代碼 (以逗號分隔)：", "SPY, QQQ, NVDA, TSLA")
    days_input = st.slider("分析未來幾天內到期的期權？", min_value=1, max_value=365, value=60)
    range_input = st.slider("履約價掃描範圍 (上下 %)", min_value=5, max_value=50, value=15, step=5, help="放大範圍可以看見更遠的支撐壓力牆，有助於尋找隱藏的 Zero Gamma。")
    risk_free_rate = st.number_input("無風險利率設定 (%)", value=4.0) / 100.0
    
    run_button = st.button("🚀 開始掃描籌碼與策略", use_container_width=True)

# --- 主程式邏輯 ---
if run_button:
    tickers = [t.strip().upper() for t in ticker_input.split(",")]
    
    if not tickers or tickers == [""]:
        st.warning("請輸入至少一個股票代碼！")
    else:
        current_time_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        today_date_str = datetime.datetime.now().strftime("%Y-%m-%d")
        
        for ticker in tickers:
            st.markdown("---")
            st.subheader(f"🎯 {ticker} 籌碼觀測站")
            
            target_date = (datetime.datetime.now() + datetime.timedelta(days=days_input)).strftime("%Y-%m-%d")
            st.caption(f"🕒 資料時間: {current_time_str} ｜ 涵蓋到期日: 即日起至 {target_date} ({days_input} 天內)")
            
            with st.spinner(f"正在掃描 {ticker} 的選擇權數據並計算進出場點..."):
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
                    
                    total_call_oi = 0
                    total_put_oi = 0
                    
                    for date_str in expirations:
                        exp_date = datetime.datetime.strptime(date_str, "%Y-%m-%d")
                        days_to_exp = (exp_date - today).days
                        
                        if days_to_exp < 0 or days_to_exp > days_input:
                            continue
                            
                        T = (days_to_exp + 0.5) / 365.0
                        bucket = get_dte_bucket(days_to_exp)
                        opt = stock.option_chain(date_str)
                        
                        calls = opt.calls.copy()
                        puts = opt.puts.copy()
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
                        st.info(f"{ticker} 在設定的天數內沒有足夠的期權數據。")
                        continue
                    
                    # 🌟 計算總 OI (用來判定流動性)
                    total_oi = total_call_oi + total_put_oi
                    pcr = total_put_oi / total_call_oi if total_call_oi > 0 else 0
                    
                    df = pd.DataFrame(gex_data)
                    df_grouped = df.groupby(['Strike', 'Type', 'Bucket'])['GEX'].sum().reset_index()
                    
                    range_pct = range_input / 100.0
                    lower_bound, upper_bound = spot_price * (1 - range_pct), spot_price * (1 + range_pct)
                    
                    df_filtered = df_grouped[(df_grouped['Strike'] >= lower_bound) & (df_grouped['Strike'] <= upper_bound)]
                    
                    df_total_by_strike = df_filtered.groupby('Strike')['GEX'].sum().reset_index().sort_values(by='Strike')
                    total_gex = df_total_by_strike['GEX'].sum() / 1e6
                    max_call_wall = df_total_by_strike[df_total_by_strike['GEX'] > 0].loc[df_total_by_strike['GEX'].idxmax()]['Strike'] if not df_total_by_strike[df_total_by_strike['GEX'] > 0].empty else 0
                    max_put_wall = df_total_by_strike[df_total_by_strike['GEX'] < 0].loc[df_total_by_strike['GEX'].idxmin()]['Strike'] if not df_total_by_strike[df_total_by_strike['GEX'] < 0].empty else 0
                    
                    zero_gamma_level = 0
                    closest_distance = float('inf')
                    for i in range(len(df_total_by_strike) - 1):
                        gex_1 = df_total_by_strike.iloc[i]['GEX']
                        gex_2 = df_total_by_strike.iloc[i+1]['GEX']
                        if (gex_1 < 0 and gex_2 > 0) or (gex_1 > 0 and gex_2 < 0):
                            avg_strike = (df_total_by_strike.iloc[i]['Strike'] + df_total_by_strike.iloc[i+1]['Strike']) / 2
                            dist = abs(avg_strike - spot_price)
                            if dist < closest_distance:
                                closest_distance = dist
                                zero_gamma_level = avg_strike
                    
                    zg_display = f"${zero_gamma_level:.2f}" if zero_gamma_level > 0 else "無明顯交界"
                    
                    history_file = f"gex_history_{ticker}.csv"
                    new_data = pd.DataFrame({
                        "Date": [today_date_str],
                        "Spot Price": [round(spot_price, 2)],
                        "Total GEX (M)": [round(total_gex, 2)],
                        "P/C Ratio": [round(pcr, 2)],
                        "Zero Gamma": [round(zero_gamma_level, 2)],
                        "Call Wall": [max_call_wall],
                        "Put Wall": [max_put_wall]
                    })
                    
                    if os.path.exists(history_file):
                        history_df = pd.read_csv(history_file)
                        if today_date_str in history_df["Date"].values:
                            history_df.loc[history_df["Date"] == today_date_str, :] = new_data.values
                        else:
                            history_df = pd.concat([history_df, new_data], ignore_index=True)
                    else:
                        history_df = new_data
                        
                    history_df.to_csv(history_file, index=False)
                    
                    # ---------------------------------------------------------
                    # 🤖 AI 策略雷達 & 流動性防呆機制
                    # ---------------------------------------------------------
                    alerts = []
                    threshold = 0.015
                    
                    # 🌟 攔截器：流動性極低警告
                    if total_oi < 50000:
                        alerts.append(("🛑 嚴重警告：期權流動性不足 (尾巴搖不動狗)", f"此標的在分析期間內的期權總未平倉量僅有 **{int(total_oi):,} 口**。這是典型的小型股或冷門股特徵。由於造市商避險資金過小，算出的 Gamma 牆極易被現貨賣壓貫破。**強烈建議您忽略此標的的 GEX 數據，改用傳統技術面與基本面分析！**", "error"))
                    else:
                        # 只有在流動性充足的情況下，才顯示其他交易策略提示
                        if is_near_opex(today):
                            alerts.append(("📅 策略五：OpEx 結算日變盤警告 (即將拔塞子)", "目前正值「每月第三個週五」的選擇權大結算前後！大量 Gamma 即將蒸發，原本壓制股價的牆壁即將失效。請密切留意結算後是否出現『單邊大方向突破』的伽馬軋空行情。", "warning"))
                        
                        if zero_gamma_level > 0 and abs(spot_price - zero_gamma_level) / spot_price <= threshold:
                            alerts.append(("⚡ 策略四：Zero Gamma 多空決戰點 (順勢切換)", f"當前股價 (${spot_price:.2f}) 正處於多空分水嶺 (${zero_gamma_level:.2f}) 邊緣！\n👉 **若帶量跌破**：莊家被迫追漲殺跌，波動爆發（適合順勢做空）。\n👉 **若強勢站穩**：警報解除，市場恢復平靜（適合波段做多）。", "info"))
                        
                        if max_put_wall > 0 and abs(spot_price - max_put_wall) / spot_price <= threshold:
                            alerts.append(("🔥 策略二：Put Wall 極限支撐 (勝率極高)", f"股價 (${spot_price:.2f}) 極度逼近最大下檔支撐牆 (${max_put_wall})！\n造市商在此有強大護盤意願。這是一個極佳的反轉買點，適合執行「賣出賣權 (Sell Put)」或尋找止跌做多機會。", "success"))
                        
                        if max_call_wall > 0 and abs(spot_price - max_call_wall) / spot_price <= threshold:
                            alerts.append(("⚠️ 策略三：Call Wall 泰山壓頂 (左側遇阻)", f"股價 (${spot_price:.2f}) 極度逼近最大上檔壓力牆 (${max_call_wall})！\n造市商將在此大量倒貨現股避險。多單請考慮獲利了結，激進者可尋找「假突破回落」的放空時機。", "warning"))

                        if total_gex > 0 and not (abs(spot_price - max_put_wall) / spot_price <= threshold) and not (abs(spot_price - max_call_wall) / spot_price <= threshold):
                            alerts.append(("💡 策略一：正 GEX 區間震盪模式 (高拋低吸)", f"目前整體市場 GEX 為正 ({total_gex:.2f} M)，造市商扮演著市場避震器。目前的最佳策略是「高拋低吸」：靠近 ${max_put_wall} 做多，靠近 ${max_call_wall} 停利/做空。", "success"))
                        elif total_gex < 0 and not (abs(spot_price - zero_gamma_level) / spot_price <= threshold):
                            alerts.append(("🚨 警告：負 GEX 狂暴模式 (順勢交易)", f"目前整體市場 GEX 為負 ({total_gex:.2f} M)！造市商正在追漲殺跌，市場極度脆弱。切勿隨便摸底，支撐極易跌破，建議縮小部位並採取「順勢交易」。", "error"))

                    if alerts:
                        st.markdown("### 🤖 AI 策略雷達：自動進出場偵測")
                        for title, desc, atype in alerts:
                            if atype == "success": st.success(f"**{title}**\n\n{desc}")
                            elif atype == "warning": st.warning(f"**{title}**\n\n{desc}")
                            elif atype == "error": st.error(f"**{title}**\n\n{desc}")
                            elif atype == "info": st.info(f"**{title}**\n\n{desc}")
                    
                    st.markdown("---")
                    
                    # --- 顯示關鍵數據儀表板 ---
                    st.markdown("##### 📊 關鍵籌碼指標")
                    col1, col2, col3 = st.columns(3)
                    col1.metric("當前股價", f"${spot_price:.2f}")
                    gex_status = "🟢 正伽馬 (護盤穩定)" if total_gex > 0 else "🔴 負伽馬 (波動放大)"
                    col2.metric("該標的總體 GEX", f"{total_gex:.2f} M", gex_status, delta_color="normal" if total_gex > 0 else "inverse")
                    
                    if pcr > 1.2: pcr_status = "🔴 極度恐慌 (醞釀軋空)"
                    elif pcr < 0.6: pcr_status = "🟢 極度貪婪 (注意回檔)"
                    else: pcr_status = "⚪ 情緒中性"
                    col3.metric("未平倉買賣權比 (P/C Ratio)", f"{pcr:.2f}", pcr_status, delta_color="off")
                    
                    col4, col5, col6 = st.columns(3)
                    col4.metric("多空分水嶺 (Zero Gamma)", zg_display)
                    col5.metric("最大上檔壓力 (Call Wall)", f"${max_call_wall}")
                    col6.metric("最大下檔支撐 (Put Wall)", f"${max_put_wall}")
                    
                    # --- 使用 Tabs 分頁顯示圖表 ---
                    tab1, tab2 = st.tabs(["🧱 GEX 期限結構分佈圖 (當日快照)", "📈 GEX 歷史趨勢與資料庫"])
                    
                    with tab1:
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
                                for _, row in call_data.iterrows():
                                    values[strike_idx_map[row['Strike']]] = row['GEX'] / 1e6
                                ax.bar(unique_strikes, values, bottom=pos_bottoms, width=1, color=call_colors[bucket], label=f'Call: {bucket}', alpha=0.9)
                                pos_bottoms += values
                                
                            put_data = df_filtered[(df_filtered['Type'] == 'Put') & (df_filtered['Bucket'] == bucket)]
                            if not put_data.empty:
                                values = np.zeros(len(unique_strikes))
                                for _, row in put_data.iterrows():
                                    values[strike_idx_map[row['Strike']]] = row['GEX'] / 1e6
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
                        
                    with tab2:
                        if len(history_df) > 1:
                            st.write("觀察總體 GEX 的膨脹與收縮，預判未來幾天的波動率方向。")
                            chart_data = history_df.set_index("Date")[["Total GEX (M)"]]
                            st.line_chart(chart_data)
                            st.dataframe(history_df, use_container_width=True)
                        else:
                            st.info("📌 這是您第一天記錄資料！請明天再次點擊計算，這裡就會自動畫出歷史趨勢折線圖。")
                            st.dataframe(history_df, use_container_width=True)
                            
                except Exception as e:
                    st.error(f"計算 {ticker} 時發生錯誤: {e}")