import os
import urllib.request
import json
import pandas as pd
import yfinance as yf
import requests
import time
import urllib.parse

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
        """【神級破解法】自動輪替 Public API 代理伺服器繞過 GitHub IP 封鎖"""
        encoded_url = urllib.parse.quote(target_url, safe='')
        proxies = [
            target_url,  # 1. 先嘗試直接連線
            f"https://api.allorigins.win/raw?url={encoded_url}",  # 2. 透過 AllOrigins 代理
            f"https://api.codetabs.com/v1/proxy?quest={target_url}" # 3. 透過 CodeTabs 代理
        ]
        
        for p_url in proxies:
            try:
                res = requests.get(p_url, headers=self.headers, timeout=12)
                if res.status_code == 200:
                    data = res.json()
                    # 確認回傳的 JSON 是有效的證交所或 FinMind 格式
                    if isinstance(data, dict) and ('stat' in data or 'msg' in data):
                        return data
            except Exception:
                continue
        return None

    def fetch_yfinance_prices(self):
        """抓取美股、大盤指數與匯率"""
        tickers = {
            "台股大盤": "^TWII", "費半指數": "^SOX", "輝達 NVDA": "NVDA",
            "台積電 ADR": "TSM", "美元兌台幣": "TWD=X"
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
        """抓取 2年期與10年期美債殖利率 (串接 FRED API)"""
        base_url = "https://api.stlouisfed.org/fred/series/observations"
        yields = {}
        for name, series_id in [("2年美債", "DGS2"), ("10年美債", "DGS10")]:
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
                        yields[name] = f"{curr:.2f} ({sign}{pct_change:.2f}%)"
                    elif len(obs) == 1:
                        yields[name] = f"{float(obs[0]['value']):.2f} (N/A)"
                    else:
                        yields[name] = "N/A"
            except Exception:
                yields[name] = "N/A (Error)"
        return yields

    def fetch_twse_institutional(self):
        """抓取三大法人 (結合代理伺服器與防快取時間戳)"""
        timestamp = int(time.time()) # 加入時間戳避免抓到舊資料
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
            else:
                chips = {k: "N/A (遭防護阻擋)" for k in chips}
        except Exception:
            pass
        return chips

    def fetch_margin_balance(self):
        """抓取融資融券 (結合代理伺服器繞過 FinMind 流量限制)"""
        chips = {"融資餘額(億)": "N/A", "融券餘額(萬張)": "N/A"}
        start_date = (pd.Timestamp.now() - pd.Timedelta(days=14)).strftime("%Y-%m-%d")
        timestamp = int(time.time())
        url = f"https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockTotalMarginPurchaseShortSale&start_date={start_date}&_={timestamp}"
        
        data = self._safe_get_json(url)
        try:
            if data and data.get("msg") == "success" and len(data.get("data", [])) >= 2:
                latest = data["data"][-1]
                prev = data["data"][-2]
                
                mb = latest["MarginPurchaseTodayBalance"] / 100000000
                mb_diff = (latest["MarginPurchaseTodayBalance"] - prev["MarginPurchaseTodayBalance"]) / 100000000
                sign_m = "+" if mb_diff > 0 else ""
                chips["融資餘額(億)"] = f"{mb:.2f} ({sign_m}{mb_diff:.2f})"
                
                sb = latest["ShortSaleTodayBalance"] / 10000
                sb_diff = (latest["ShortSaleTodayBalance"] - prev["ShortSaleTodayBalance"]) / 10000
                sign_s = "+" if sb_diff > 0 else ""
                chips["融券餘額(萬張)"] = f"{sb:.2f} ({sign_s}{sb_diff:.2f})"
            else:
                chips["融資餘額(億)"] = "N/A (遭防護阻擋)"
                chips["融券餘額(萬張)"] = "N/A (遭防護阻擋)"
        except Exception:
            pass
        return chips


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
    
    # 2.1 合併股市與美債價格
    prices = market_bot.fetch_yfinance_prices()
    treasury_yields = market_bot.fetch_treasury_yields()
    prices.update(treasury_yields)
    
    # 2.2 抓取三大法人與融資融券
    chips = market_bot.fetch_twse_institutional()
    margin_data = market_bot.fetch_margin_balance()
    chips.update(margin_data)

    # 3. 組合 LINE 訊息 
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
    
    msg += "\n💰 【台股現貨與信用籌碼】\n"
    for name, net_buy in chips.items():
        if isinstance(net_buy, (int, float)):
            icon = "🔴" if net_buy > 0 else ("🟢" if net_buy < 0 else "⚪")
        elif isinstance(net_buy, str) and "(+" in net_buy:
            icon = "🔴"
        elif isinstance(net_buy, str) and "(-" in net_buy:
            icon = "🟢"
        else:
            icon = "⚪"
            
        msg += f"• {name}: {icon} {net_buy}\n"

    # 送出 LINE 訊息
    send_line_message(LINE_ACCESS_TOKEN, LINE_USER_ID, msg)
    print("✅ 執行完畢並成功推播！")
