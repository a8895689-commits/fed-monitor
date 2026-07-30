import os
import urllib.request
import json
import pandas as pd
import yfinance as yf
import requests
import time
import urllib.parse
import datetime
import io

# 關閉 pandas 警告
pd.options.mode.chained_assignment = None

class LiveFedPredictor:
    """主題一：聯準會利率決策預測模組 (串接 FRED API)"""
    def __init__(self, api_key):
        self.api_key = api_key
        self.base_url = "https://api.stlouisfed.org/fred/series/observations"

    def _get_latest_obs(self, series_id, units="lin"):
        url = f"{self.base_url}?series_id={series_id}&api_key={self.api_key}&file_type=json&sort_order=desc&limit=10&units={units}"
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req) as response:
                data = json.loads(response.read().decode('utf-8'))
                valid_obs = [obs for obs in data.get('observations', []) if obs.get('value', '.') != '.']
                
                if len(valid_obs) >= 2:
                    return float(valid_obs[0]['value']), float(valid_obs[1]['value']), valid_obs[0]['date']
                elif len(valid_obs) == 1:
                    return float(valid_obs[0]['value']), None, valid_obs[0]['date']
        except Exception:
            pass
        return None, None, None

    def analyze(self):
        metrics = [
            ("Core PCE", "PCEPILFE", "pc1", 0.25, lambda x: "Hike" if x > 3.2 else ("Cut" if x < 2.3 else "Hold"), "升>3.2 / 降<2.3"),
            ("ECI 薪資", "ECIALLCIV", "pc1", 0.20, lambda x: "Hike" if x > 4.0 else ("Cut" if x < 2.8 else "Hold"), "升>4.0 / 降<2.8"),
            ("JOLTS 求供比", "JOLTS_RATIO", "lin", 0.15, lambda x: "Hike" if x > 1.2 else ("Cut" if x < 0.8 else "Hold"), "升>1.2 / 降<0.8"),
            ("薩姆規則", "SAHMREALTIME", "lin", 0.15, lambda x: "Cut" if x >= 0.50 else "Hold", "降≥0.50"),
            ("NFCI 金融條件", "NFCI", "lin", 0.15, lambda x: "Hike" if x < -0.7 else ("Cut" if x > 0.5 else "Hold"), "升<-0.7 / 降>0.5"),
            ("5Y 通膨預期", "T5YIE", "lin", 0.10, lambda x: "Hike" if x > 2.5 else ("Cut" if x < 2.0 else "Hold"), "升>2.5 / 降<2.0")
        ]
        
        results, dates = [], []
        hike_score, cut_score, hold_score = 0.0, 0.0, 0.0
        
        for name, sid, unit, weight, eval_fn, condition_str in metrics:
            if sid == "JOLTS_RATIO":
                jol_val1, jol_val2, d1 = self._get_latest_obs("JTSJOL", "lin")
                unemp_val1, unemp_val2, d2 = self._get_latest_obs("UNEMPLOY", "lin")
                if jol_val1 and unemp_val1 and unemp_val1 > 0:
                    val = jol_val1 / unemp_val1
                    prev_val = (jol_val2 / unemp_val2) if (jol_val2 and unemp_val2 and unemp_val2 > 0) else None
                    date = max(d1, d2)
                else:
                    val, prev_val, date = None, None, None
            else:
                val, prev_val, date = self._get_latest_obs(sid, units=unit)
            
            if val is not None:
                if date: dates.append(date)
                signal = eval_fn(val)
                diff_str = ""
                if prev_val is not None:
                    diff = val - prev_val
                    sign = "+" if diff > 0 else ""
                    diff_str = f" ({sign}{diff:.2f})"

                if signal == "Hike": 
                    hike_score += weight; sig_txt = "🔴 升息"
                elif signal == "Cut": 
                    cut_score += weight; sig_txt = "🟢 降息"
                else: 
                    hold_score += weight; sig_txt = "🟡 持平"
                    
                results.append({"指標": name, "數值": f"{val:.2f}{diff_str}", "判定": sig_txt, "門檻": condition_str})
            else:
                results.append({"指標": name, "數值": "N/A", "判定": "❓ 缺資料", "門檻": condition_str})

        total = hike_score + cut_score + hold_score
        prob = {
            "升息": round((hike_score / total) * 100, 1) if total > 0 else 0,
            "降息": round((cut_score / total) * 100, 1) if total > 0 else 0,
            "持平": round((hold_score / total) * 100, 1) if total > 0 else 0
        }
        latest_date = max(dates) if dates else "未知"
        return results, prob, latest_date


class DailyMarketTracker:
    """主題二：台美股價與台股籌碼自動化模組"""
    def __init__(self, fred_api_key):
        self.fred_api_key = fred_api_key
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        }

    def _safe_get_json(self, target_url):
        encoded_url = urllib.parse.quote(target_url, safe='')
        proxies = [
            target_url, 
            f"https://api.allorigins.win/raw?url={encoded_url}", 
            f"https://api.codetabs.com/v1/proxy?quest={target_url}" 
        ]
        for p_url in proxies:
            try:
                res = requests.get(p_url, headers=self.headers, timeout=12)
                if res.status_code == 200:
                    data = res.json()
                    if isinstance(data, dict) and ('stat' in data or 'msg' in data):
                        return data
            except Exception:
                continue
        return None

    def fetch_yfinance_prices(self):
        tickers = {
            "台股大盤": "^TWII",
            "費半指數": "^SOX",
            "輝達 NVDA": "NVDA",
            "台積電 ADR": "TSM",
            "WTI 原油": "CL=F",            
            "布蘭特原油": "BZ=F",          
            "10年美債(價格)": "ZN=F",      
            "2年美債(價格)": "ZT=F",       
            "美元兌台幣": "TWD=X"
        }
        prices = {}
        for name, symbol in tickers.items():
            try:
                stock = yf.Ticker(symbol)
                hist = stock.history(period="5d")
                if len(hist) >= 2:
                    current_price = hist['Close'].iloc[-1]
                    prev_price = hist['Close'].iloc[-2]
                    pct_change = ((current_price - prev_price) / prev_price) * 100
                    sign = "+" if pct_change > 0 else ""
                    prices[name] = f"{current_price:.2f} ({sign}{pct_change:.2f}%)"
                elif len(hist) == 1:
                    prices[name] = f"{hist['Close'].iloc[-1]:.2f} (N/A)"
                else:
                    prices[name] = "N/A"
            except Exception:
                prices[name] = "N/A (Error)"
        return prices

    def fetch_treasury_yields(self):
        base_url = "https://api.stlouisfed.org/fred/series/observations"
        yields = {}
        for name, series_id in [("2年美債(殖利率)", "DGS2"), ("10年美債(殖利率)", "DGS10")]:
            url = f"{base_url}?series_id={series_id}&api_key={self.fred_api_key}&file_type=json&sort_order=desc&limit=5&units=lin"
            try:
                req = urllib.request.Request(url, headers=self.headers)
                with urllib.request.urlopen(req) as response:
                    data = json.loads(response.read().decode('utf-8'))
                    obs = [o for o in data.get('observations', []) if o.get('value', '.') != '.']
                    if len(obs) >= 2:
                        curr = float(obs[0]['value'])
                        prev = float(obs[1]['value'])
                        pct_change = ((curr - prev) / prev) * 100
                        sign = "+" if pct_change > 0 else ""
                        yields[name] = f"{curr:.2f}% ({sign}{pct_change:.2f}%)"
                    else:
                        yields[name] = "N/A"
            except Exception:
                yields[name] = "N/A (Error)"
        return yields

    def fetch_twse_institutional(self):
        timestamp = int(time.time()) 
        url = f"https://www.twse.com.tw/fund/BFI82U?response=json&type=day&_={timestamp}"
        chips = {"外資及陸資": "N/A", "投信": "N/A", "自營商(自行)": "N/A", "自營商(避險)": "N/A"}
        data = self._safe_get_json(url)
        try:
            if data and data.get('stat') == 'OK':
                for row in data['data']:
                    name = row[0].strip()
                    net_buy = round(int(row[3].replace(',', '')) / 100000000, 2)
                    if "外資及陸資" in name: chips["外資及陸資"] = net_buy
                    elif "投信" in name: chips["投信"] = net_buy
                    elif "自營商(自行買賣)" in name: chips["自營商(自行)"] = net_buy
                    elif "自營商(避險)" in name: chips["自營商(避險)"] = net_buy
        except Exception:
            pass
        return chips


class TaiwanDerivativesTrackerTaifex:
    """主題三 (方案B)：台指期權進階籌碼 (直接串接期交所 API - 強化容錯版)"""
    def __init__(self):
        self.headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

    def get_taifex_csv(self, url, payload):
        try:
            res = requests.post(url, data=payload, headers=self.headers, timeout=10)
            if res.status_code != 200: return pd.DataFrame()
            res.encoding = 'big5'
            csv_text = res.text
            
            # 讀取 CSV 並略過結尾可能出現的備註干擾
            df = pd.read_csv(io.StringIO(csv_text), on_bad_lines='skip')
            df.columns = df.columns.str.strip().str.replace(' ', '')
            
            # 【關鍵修復 1】: 統一日期欄位名稱
            for date_col in ['交易日期', 'Date', '日期']:
                if date_col in df.columns and date_col != '日期':
                    df.rename(columns={date_col: '日期'}, inplace=True)
            
            if '日期' not in df.columns: return pd.DataFrame()
            
            # 【關鍵修復 2】: 使用 pd.to_datetime 自動過濾垃圾資料 (將備註文字轉為 NaT 並剔除)
            df['日期'] = pd.to_datetime(df['日期'], errors='coerce')
            df = df.dropna(subset=['日期'])
            # 統一標準化為 YYYY/MM/DD 格式，這樣不管是 7/30 還是 07/30 都不會比對失敗
            df['日期'] = df['日期'].dt.strftime('%Y/%m/%d')
            
            for col in df.columns:
                if df[col].dtype == 'object': 
                    df[col] = df[col].str.strip()
            return df
        except:
            return pd.DataFrame()

    def fetch_data(self):
        end_date = datetime.datetime.now()
        start_date = end_date - datetime.timedelta(days=15)
        d_start = start_date.strftime("%Y/%m/%d")
        d_end = end_date.strftime("%Y/%m/%d")

        def pl(cid): 
            return {"queryStartDate": d_start, "queryEndDate": d_end, 
                    "commodityId": cid, "commodity_id": cid, "down_type": "1"}

        # 1. 抓取 PCR
        df_pcr = self.get_taifex_csv("https://www.taifex.com.tw/cht/3/pcRatioDown", pl(""))
        if df_pcr.empty or '日期' not in df_pcr.columns:
            return "\n⚠️ 無法取得期交所籌碼數據(PCR)，請檢查網路或稍後再試\n"
        
        dates = sorted(df_pcr['日期'].unique(), reverse=True)
        if len(dates) < 2: return "\n⚠️ 期交所籌碼歷史天數不足\n"
        d1, d2 = dates[0], dates[1]

        # 2. 抓取外資選擇權與期貨資料
        df_opt = self.get_taifex_csv("https://www.taifex.com.tw/cht/3/callsAndPutsDateDown", pl("TXO"))
        df_mtx_inst = self.get_taifex_csv("https://www.taifex.com.tw/cht/3/futContractsDateDown", pl("MTX"))
        df_tmf_inst = self.get_taifex_csv("https://www.taifex.com.tw/cht/3/futContractsDateDown", pl("TMF"))
        
        # 3. 抓取全市場期貨總未平倉量
        df_mtx_daily = self.get_taifex_csv("https://www.taifex.com.tw/cht/3/futDataDown", pl("MTX"))
        df_tmf_daily = self.get_taifex_csv("https://www.taifex.com.tw/cht/3/futDataDown", pl("TMF"))

        # 4. 抓取 VIX 指數
        df_vix = pd.DataFrame()
        try:
            res_vix = requests.post("https://www.taifex.com.tw/cht/7/vixData", data=pl(""), headers=self.headers, timeout=10)
            res_vix.encoding = 'utf-8'
            dfs = pd.read_html(io.StringIO(res_vix.text))
            for d in dfs:
                d.columns = d.columns.str.strip().str.replace(' ', '')
                for date_col_name in ['交易日期', 'Date']:
                    if date_col_name in d.columns:
                        d.rename(columns={date_col_name: '日期'}, inplace=True)
                        break
                        
                if '日期' in d.columns and '收盤指數' in d.columns:
                    # 【關鍵修復 3】: VIX 表格同樣使用 pd.to_datetime 過濾垃圾資訊
                    d['日期'] = pd.to_datetime(d['日期'], errors='coerce')
                    d = d.dropna(subset=['日期'])
                    d['日期'] = d['日期'].dt.strftime('%Y/%m/%d')
                    
                    df_vix = d
                    break
        except:
            pass

        # ===== 安全型別轉換 =====
        def s_int(val): return int(str(val).replace(',', '')) if pd.notna(val) else 0
        def s_flt(val): return float(str(val).replace(',', '')) if pd.notna(val) else 0.0

        # ===== 資料提取與計算邏輯 =====
        def get_pcr(d_str):
            if df_pcr.empty: return 0.0
            sub = df_pcr[df_pcr['日期'] == d_str]
            # 優先抓未平倉量比率
            pcr_col = next((c for c in df_pcr.columns if '未平倉' in c and ('比率' in c or 'Ratio' in c)), None)
            if not pcr_col: pcr_col = next((c for c in df_pcr.columns if '比率' in c or 'Ratio' in c), None)
            return s_flt(sub[pcr_col].values[0]) if not sub.empty and pcr_col else 0.0

        def get_vix(d_str):
            if df_vix.empty: return 0.0
            sub = df_vix[df_vix['日期'] == d_str]
            return s_flt(sub['收盤指數'].values[0]) if not sub.empty and '收盤指數' in sub.columns else 0.0

        def get_opt_foreign(d_str, cp):
            if df_opt.empty: return 0
            cp_col = next((col for col in df_opt.columns if '買賣權' in col), None)
            if not cp_col: return 0
            
            sub = df_opt[(df_opt['日期'] == d_str) & 
                         (df_opt['身份別'].astype(str).str.contains('外資', na=False)) & 
                         (df_opt[cp_col].astype(str).str.contains(cp, na=False))]
                         
            net_col = next((c for c in df_opt.columns if '未平倉淨' in c), None)
            if not net_col: net_col = next((c for c in df_opt.columns if '淨額' in c), None)
            
            return s_int(sub[net_col].values[0]) if not sub.empty and net_col else 0

        def get_ret_net(df, d_str):
            if df.empty: return 0
            sub = df[df['日期'] == d_str] 
            
            net_col = next((c for c in df.columns if '未平倉淨' in c), None)
            if not net_col: net_col = next((c for c in df.columns if '淨額' in c), None)
            if not net_col: return 0
            
            return -sum([s_int(x) for x in sub[net_col].values]) if not sub.empty else 0

        def get_tot(df, d_str):
            if df.empty: return 1
            sub = df[df['日期'] == d_str] 
            
            if '交易時段' in sub.columns: 
                sub = sub[sub['交易時段'].astype(str).str.contains('一般', na=False)] 
            
            oi_col = next((c for c in df.columns if '未沖銷' in c or '未平倉' in c), None)
            if not sub.empty and oi_col: 
                return sum([s_int(x) for x in sub[oi_col].values])
            return 1

        pcr_1, pcr_2 = get_pcr(d1), get_pcr(d2)
        vix_1, vix_2 = get_vix(d1), get_vix(d2)
        
        fc_1, fc_2 = get_opt_foreign(d1, '買權'), get_opt_foreign(d2, '買權')
        fp_1, fp_2 = get_opt_foreign(d1, '賣權'), get_opt_foreign(d2, '賣權')
        
        rm_1, rm_2 = get_ret_net(df_mtx_inst, d1), get_ret_net(df_mtx_inst, d2)
        rt_1, rt_2 = get_ret_net(df_tmf_inst, d1), get_ret_net(df_tmf_inst, d2)
        
        tm_1, tm_2 = get_tot(df_mtx_daily, d1), get_tot(df_mtx_daily, d2)
        tt_1, tt_2 = get_tot(df_tmf_daily, d1), get_tot(df_tmf_daily, d2)

        # 散戶多空比 = 散戶淨未平倉 / 總未平倉
        rr_m_1 = (rm_1 / tm_1) * 100 if tm_1 > 1 else 0.0
        rr_m_2 = (rm_2 / tm_2) * 100 if tm_2 > 1 else 0.0
        rr_t_1 = (rt_1 / tt_1) * 100 if tt_1 > 1 else 0.0
        rr_t_2 = (rt_2 / tt_2) * 100 if tt_2 > 1 else 0.0

        # ===== 排版輸出 =====
        def s_str(v): return f"+{v}" if v > 0 else str(v)
        def s_fstr(v): return f"+{v:.2f}" if v > 0 else f"{v:.2f}"
        def ar(c, p): return "↗" if c > p else ("↘" if c < p else "→")

        msg = "\n📊 【台指進階籌碼 (散戶/外資/VIX)】\n"
        msg += f"📅 資料日期: {d1}\n"
        msg += f"• 外資買權淨未平倉: {s_str(fc_1)} (增減 {s_str(fc_1 - fc_2)})\n"
        msg += f"• 外資賣權淨未平倉: {s_str(fp_1)} (增減 {s_str(fp_1 - fp_2)})\n"
        msg += f"• 散戶小台淨未平倉: {s_str(rm_1)} (增減 {s_str(rm_1 - rm_2)})\n"
        msg += f"• 散戶微台淨未平倉: {s_str(rt_1)} (增減 {s_str(rt_1 - rt_2)})\n"
        msg += f"• 小台散戶多空比: {s_fstr(rr_m_2)}% {ar(rr_m_1, rr_m_2)} {s_fstr(rr_m_1)}%\n"
        msg += f"• 微台散戶多空比: {s_fstr(rr_t_2)}% {ar(rr_t_1, rr_t_2)} {s_fstr(rr_t_1)}%\n"
        msg += f"• 全市場Put/Call Ratio: {pcr_2:.2f}% {ar(pcr_1, pcr_2)} {pcr_1:.2f}%\n"
        msg += f"• VIX指標: {vix_2:.2f} {ar(vix_1, vix_2)} {vix_1:.2f}\n"

        return msg

class TaiwanOptionsTracker:
    """主題四：台指選擇權莊家籌碼與痛點計算"""
    def __init__(self):
        self.headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

    def get_twse_close(self, date_str):
        twse_date = date_str.replace('/', '') 
        url = f"https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?date={twse_date}&type=IND&response=json"
        try:
            res = requests.get(url, headers=self.headers, timeout=8)
            data = res.json()
            rows = data.get('data1', [])
            if not rows and 'tables' in data:
                for t in data['tables']:
                    if 'data' in t: rows.extend(t['data'])
            for row in rows:
                if len(row) > 1 and '發行量加權股價指數' in str(row[0]):
                    return float(str(row[1]).replace(',', ''))
        except Exception:
            try:
                hist = yf.Ticker("^TWII").history(period="5d")
                return float(hist['Close'].iloc[-1])
            except:
                pass
        return 0

    def get_taifex_csv(self, date_obj):
        date_str = date_obj.strftime("%Y/%m/%d")
        url = "https://www.taifex.com.tw/cht/3/optDataDown"
        payload = {
            "down_type": "1", "commodity_id": "TXO", "commodity_id2": "",
            "queryStartDate": date_str, "queryEndDate": date_str, "macrostype": "siron"
        }
        try:
            response = requests.post(url, data=payload, headers=self.headers, timeout=10)
            if response.status_code != 200: return pd.DataFrame()
            response.encoding = 'big5'
            csv_text = response.text
            if len(csv_text) < 500 or "交易日期" not in csv_text: return pd.DataFrame()
            return pd.read_csv(io.StringIO(csv_text))
        except:
            return pd.DataFrame()

    def find_column(self, df, keywords):
        for col in df.columns:
            for kw in keywords:
                if kw in col: return col
        return None

    def clean_df(self, df):
        if df.empty: return df
        df.columns = df.columns.str.strip().str.replace(' ', '')
        for col in df.columns:
            if df[col].dtype == 'object': df[col] = df[col].astype(str).str.strip()
        contract_col = self.find_column(df, ['契約'])
        session_col = self.find_column(df, ['時段'])
        if contract_col: df = df[df[contract_col] == 'TXO']
        # 【修復重點】：加入 .astype(str) 防呆轉換
        if session_col: df = df[df[session_col].astype(str).str.contains('一般', na=False)]
        return df

    def format_payout(self, total_pain_points):
        points_10k = total_pain_points / 10000
        cash_100m = (total_pain_points * 50) / 100000000 
        return f"{points_10k:.2f}萬 ({cash_100m:.2f}億)"

    def analyze_options(self):
        today = datetime.datetime.now()
        valid_dfs = {}
        
        for i in range(12):
            test_date = today - datetime.timedelta(days=i)
            df = self.get_taifex_csv(test_date)
            if not df.empty:
                date_str = test_date.strftime("%Y/%m/%d")
                valid_dfs[date_str] = df
            if len(valid_dfs) == 2:
                break
                
        if len(valid_dfs) < 1:
            return "⚠️ 無法取得期交所選擇權資料"

        dates_found = list(valid_dfs.keys())
        curr_date = dates_found[0]
        curr_df = self.clean_df(valid_dfs[curr_date])
        prev_df = self.clean_df(valid_dfs[dates_found[1]]) if len(dates_found) > 1 else pd.DataFrame()
        taiex_close = self.get_twse_close(curr_date)

        expiry_col = self.find_column(curr_df, ['到期', '月份'])
        strike_col = self.find_column(curr_df, ['履約'])
        cp_col = self.find_column(curr_df, ['買賣'])
        oi_col = self.find_column(curr_df, ['未沖銷', '未平倉'])
        
        contracts = sorted(curr_df[expiry_col].unique())
        all_results = []
        
        for c in contracts:
            df_sub = curr_df[curr_df[expiry_col] == c].copy()
            df_sub[strike_col] = pd.to_numeric(df_sub[strike_col], errors='coerce')
            df_sub[oi_col] = pd.to_numeric(df_sub[oi_col].astype(str).str.replace(',', ''), errors='coerce').fillna(0)
            total_oi = df_sub[oi_col].sum()
            if total_oi < 1000: continue
                
            prev_oi = 0
            if not prev_df.empty:
                prev_sub = prev_df[prev_df[expiry_col] == c].copy()
                if not prev_sub.empty:
                    prev_sub[oi_col] = pd.to_numeric(prev_sub[oi_col].astype(str).str.replace(',', ''), errors='coerce').fillna(0)
                    prev_oi = prev_sub[oi_col].sum()
            oi_change = int(total_oi - prev_oi)
            oi_change_str = f"+{oi_change}" if oi_change > 0 else str(oi_change)
                
            # 【修復重點】：加入 .astype(str) 防呆轉換
            calls = df_sub[df_sub[cp_col].astype(str).str.contains('Call|買', case=False)].set_index(strike_col)[oi_col].to_dict()
            puts = df_sub[df_sub[cp_col].astype(str).str.contains('Put|賣', case=False)].set_index(strike_col)[oi_col].to_dict()
            
            max_call_strike = max(calls, key=calls.get) if calls else "無"
            max_put_strike = max(puts, key=puts.get) if puts else "無"
            
            strike_prices = sorted(df_sub[strike_col].dropna().unique())
            pain_values = []
            for settle_price in strike_prices:
                total_pain = 0
                for strike, oi in calls.items():
                    if settle_price > strike: total_pain += (settle_price - strike) * oi
                for strike, oi in puts.items():
                    if settle_price < strike: total_pain += (strike - settle_price) * oi
                pain_values.append({'Strike_Price': settle_price, 'Total_Pain': total_pain})
                
            pain_df = pd.DataFrame(pain_values)
            if pain_df.empty or len(pain_df) < 3: continue
                
            sorted_pain = pain_df.sort_values(by='Total_Pain')
            p1 = int(sorted_pain.iloc[0]['Strike_Price'])
            p1_str = self.format_payout(sorted_pain.iloc[0]['Total_Pain'])
            
            # 近大盤痛點
            if taiex_close > 0:
                near_zone_df = pain_df[abs(pain_df['Strike_Price'] - taiex_close) <= 600]
                if not near_zone_df.empty:
                    near_row = near_zone_df.sort_values(by='Total_Pain').iloc[0]
                    p_near = int(near_row['Strike_Price'])
                    p_near_str = self.format_payout(near_row['Total_Pain'])
                else:
                    pain_df_copy = pain_df.copy()
                    pain_df_copy['dist'] = abs(pain_df_copy['Strike_Price'] - taiex_close)
                    near_row = pain_df_copy.sort_values(by='dist').iloc[0]
                    p_near = int(near_row['Strike_Price'])
                    p_near_str = self.format_payout(near_row['Total_Pain'])
            else:
                p_near = p1; p_near_str = p1_str
            
            all_results.append({
                'contract': c, 'total_oi': int(total_oi), 'oi_change_str': oi_change_str,
                'max_call': max_call_strike, 'max_put': max_put_strike,
                'p1': p1, 'p1_str': p1_str, 'p_near': p_near, 'p_near_str': p_near_str
            })
            
        all_results.sort(key=lambda x: x['total_oi'], reverse=True)
        top_2 = all_results[:2]
        
        # 組合 LINE 專用的選擇權字串
        msg = f"\n🎯 【台指選擇權莊家佈局】\n"
        msg += f"📅 結算日資料: {curr_date}\n"
        msg += f"📊 大盤收盤價: {taiex_close}\n"
        
        for idx, c in enumerate(top_2):
            msg += f"\n📌 [合約: {c['contract']}]\n"
            msg += f"• 總OI: {c['total_oi']} (增減 {c['oi_change_str']})\n"
            msg += f"• 支撐(Put): {c['max_put']} | 壓力(Call): {c['max_call']}\n"
            msg += f"• 主痛點: {c['p1']} [{c['p1_str']}]\n"
            msg += f"• 近大盤痛點: {c['p_near']} [{c['p_near_str']}]\n"
            
        return msg


def send_line_message(token, user_id, text):
    url = 'https://api.line.me/v2/bot/message/push'
    headers = {'Content-Type': 'application/json', 'Authorization': f'Bearer {token}'}
    data = {"to": user_id, "messages": [{"type": "text", "text": text}]}
    req = urllib.request.Request(url, data=json.dumps(data).encode('utf-8'), headers=headers, method='POST')
    urllib.request.urlopen(req)

if __name__ == "__main__":
    FRED_API_KEY = os.environ.get("FRED_API_KEY")
    LINE_ACCESS_TOKEN = os.environ.get("LINE_ACCESS_TOKEN")
    LINE_USER_ID = os.environ.get("LINE_USER_ID")

    if not all([FRED_API_KEY, LINE_ACCESS_TOKEN, LINE_USER_ID]):
        print("❌ 錯誤：找不到環境變數")
        exit(1)

    # 1. 抓取美聯儲數據
    fed_bot = LiveFedPredictor(api_key=FRED_API_KEY)
    fed_results, prob, latest_date = fed_bot.analyze()

    # 2. 抓取市場價格與籌碼數據
    market_bot = DailyMarketTracker(fred_api_key=FRED_API_KEY)
    prices = market_bot.fetch_yfinance_prices()
    treasury_yields = market_bot.fetch_treasury_yields()
    prices.update(treasury_yields)
    chips = market_bot.fetch_twse_institutional()
    
    # 3. 抓取台指期權進階籌碼
    derivatives_bot = TaiwanDerivativesTrackerTaifex()
    derivatives_msg = derivatives_bot.fetch_data()

    # 4. 抓取台指選擇權莊家痛點資料
    opt_bot = TaiwanOptionsTracker()
    opt_msg = opt_bot.analyze_options()

    # 5. 組合最終 LINE 訊息
    msg = "📊 【聯準會決策儀表板】\n"
    msg += f"📅 數據發布: {latest_date}\n\n"
    for res in fed_results:
        msg += f"• {res['指標']}: {res['數值']} [{res['判定']}] (門檻: {res['門檻']})\n"

    msg += "\n🎯 升降息機率預估：\n"
    msg += f"🔴 升息: {prob['升息']}% | 🟡 持平: {prob['持平']}% | 🟢 降息: {prob['降息']}%\n\n"
    
    msg += "======================\n"
    msg += "📈 【全球核心資產報價】\n"
    for name, price in prices.items():
        msg += f"• {name}: {price}\n"
    
    msg += "\n💰 【台股三大法人現貨籌碼】\n"
    for name, net_buy in chips.items():
        icon = "🔴" if isinstance(net_buy, (int, float)) and net_buy > 0 else ("🟢" if isinstance(net_buy, (int, float)) and net_buy < 0 else "⚪")
        msg += f"• {name}: {icon} {net_buy}\n"
        
    msg += "======================"
    msg += derivatives_msg
    msg += "======================"
    msg += opt_msg

    # 送出 LINE 訊息
    send_line_message(LINE_ACCESS_TOKEN, LINE_USER_ID, msg)
    print("✅ 執行完畢並成功推播全部資訊！")
