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
            
            # 【關鍵修復】: 強制將不同名稱的日期欄位統一轉換為 '日期'
            for date_col in ['交易日期', 'Date']:
                if date_col in df.columns:
                    df.rename(columns={date_col: '日期'}, inplace=True)
            
            # 檢查是否有最終標準化的 '日期' 欄位
            if '日期' not in df.columns: return pd.DataFrame()
            
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

        # 輔助產生 Payload 參數
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

        # 4. 抓取 VIX 指數 (從網頁表格提取)
        df_vix = pd.DataFrame()
        try:
            res_vix = requests.post("https://www.taifex.com.tw/cht/7/vixData", data=pl(""), headers=self.headers, timeout=10)
            res_vix.encoding = 'utf-8'
            dfs = pd.read_html(io.StringIO(res_vix.text))
            for d in dfs:
                d.columns = d.columns.str.strip().str.replace(' ', '')
                # 同樣對 VIX 表格進行日期標準化
                for date_col_name in ['交易日期', 'Date']:
                    if date_col_name in d.columns:
                        d.rename(columns={date_col_name: '日期'}, inplace=True)
                        break
                        
                if '日期' in d.columns and '收盤指數' in d.columns:
                    df_vix = d
                    break
        except:
            pass

        # ===== 安全型別轉換 =====
        def s_int(val): return int(str(val).replace(',', '')) if pd.notna(val) else 0
        def s_flt(val): return float(str(val).replace(',', '')) if pd.notna(val) else 0.0

        # ===== 資料提取與計算邏輯 =====
        def get_pcr(d_str):
            sub = df_pcr[df_pcr['日期'] == d_str]
            # 動態找尋 pcr 欄位
            pcr_col = next((c for c in df_pcr.columns if '比率' in c or 'Ratio' in c), None)
            return s_flt(sub[pcr_col].values[0]) if not sub.empty and pcr_col else 0.0

        def get_
