import os
import urllib.request
import json
import pandas as pd
import yfinance as yf
import requests

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
                for obs in data.get('observations', []):
                    if obs.get('value', '.') != '.':
                        return float(obs.get('value')), obs.get('date')
        except Exception:
            pass
        return None, None

    def analyze(self):
        # 擴充後的指標庫 (權重總和為 1.0)
        metrics = [
            ("Core PCE", "PCEPILFE", "pc1", 0.25, lambda x: "Hike" if x > 3.2 else ("Cut" if x < 2.3 else "Hold")),
            ("ECI 薪資", "ECIALLCIV", "pc1", 0.20, lambda x: "Hike" if x > 4.0 else ("Cut" if x < 2.8 else "Hold")),
            ("JOLTS 求供比", "JOLTS_RATIO", "lin", 0.15, lambda x: "Hike" if x > 1.2 else ("Cut" if x < 0.8 else "Hold")),
            ("薩姆規則", "SAHMREALTIME", "lin", 0.15, lambda x: "Cut" if x >= 0.50 else "Hold"),
            ("NFCI 金融條件", "NFCI", "lin", 0.15, lambda x: "Hike" if x < -0.7 else ("Cut" if x > 0.5 else "Hold")),
            ("5Y 通膨預期", "T5YIE", "lin", 0.10, lambda x: "Hike" if x > 2.5 else ("Cut" if x < 2.0 else "Hold"))
        ]
        
        results, dates = [], []
        hike_score, cut_score, hold_score = 0.0, 0.0, 0.0
        
        for name, sid, unit, weight, eval_fn in metrics:
            # 針對 JOLTS 求供比進行客製化計算 (職缺數 / 失業人數)
            if sid == "JOLTS_RATIO":
                jol, d1 = self._get_latest_obs("JTSJOL", "lin")
                unemp, d2 = self._get_latest_obs("UNEMPLOY", "lin")
                if jol and unemp and unemp > 0:
                    val = jol / unemp
                    date = max(d1, d2)
                else:
                    val, date = None, None
            else:
                val, date = self._get_latest_obs(sid, units=unit)
            
            if val is not None:
                if date: dates.append(date)
                signal = eval_fn(val)
                if signal == "Hike": 
                    hike_score += weight
                    sig_txt = "🔴 升息"
                elif signal == "Cut": 
                    cut_score += weight
                    sig_txt = "🟢 降息"
                else: 
                    hold_score += weight
                    sig_txt = "🟡 持平"
                results.append({"指標": name, "數值": f"{val:.2f}", "判定": sig_txt})
            else:
                results.append({"指標": name, "數值": "N/A", "判定": "❓ 缺資料"})

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
    def __init__(self):
        self.headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}

    def fetch_yfinance_prices(self):
        """抓取美股與大盤指數 (含漲跌幅計算)"""
        tickers = {
            "台股大盤": "^TWII",
            "費半指數": "^SOX",
            "輝達 NVDA": "NVDA",
            "台積電 ADR": "TSM"
        }
        prices = {}
        for name, symbol in tickers.items():
            try:
                stock = yf.Ticker(symbol)
                # 抓取最近 5 天資料，確保能拿到前一個交易日的收盤價
                hist = stock.history(period="5d")
                
                if len(hist) >= 2:
                    current_price = hist['Close'].iloc[-1]
                    prev_price = hist['Close'].iloc[-2]
                    
                    # 計算漲跌幅 %
                    pct_change = ((current_price - prev_price) / prev_price) * 100
                    
                    # 格式化字串 (正數加上 + 號，負數自帶 - 號)
                    sign = "+" if pct_change > 0 else ""
                    prices[name] = f"{current_price:.2f} ({sign}{pct_change:.2f}%)"
                    
                elif len(hist) == 1:
                    # 萬一只有一天資料的防呆機制
                    current_price = hist['Close'].iloc[-1]
                    prices[name] = f"{current_price:.2f} (N/A)"
                else:
                    prices[name] = "N/A"
            except Exception:
                prices[name] = "N/A (Error)"
        return prices

    def fetch_twse_institutional(self):
        """抓取台灣證交所三大法人買賣超 (單位: 億元)"""
        url = "https://www.twse.com.tw/fund/BFI82U?response=json&type=day"
        chips = {"外資及陸資": "N/A", "投信": "N/A", "自營商(自行)": "N/A", "自營商(避險)": "N/A"}
        try:
            res = requests.get(url, headers=self.headers, timeout=5)
            data = res.json()
            if data.get('stat') == 'OK':
                for row in data['data']:
                    name = row[0].strip()
                    net_buy = round(int(row[3].replace(',', '')) / 100000000, 2)
                    if "外資及陸資" in name: chips["外資及陸資"] = net_buy
                    elif "投信" in name: chips["投信"] = net_buy
                    elif "自營商(自行買賣)" in name: chips["自營商(自行)"] = net_buy
                    elif "自營商(避險)" in name: chips["自營商(避險)"] = net_buy
        except Exception:
            chips = {k: "N/A (遭阻擋)" for k in chips}
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

    # 1. 抓取美聯儲數據 (包含最新更新日期)
    fed_bot = LiveFedPredictor(api_key=FRED_API_KEY)
    fed_results, prob, latest_date = fed_bot.analyze()

    # 2. 抓取市場價格與籌碼數據
    market_bot = DailyMarketTracker()
    prices = market_bot.fetch_yfinance_prices()
    chips = market_bot.fetch_twse_institutional()

    # 3. 組合 LINE 訊息 
    msg = "📊 【聯準會決策儀表板】\n"
    msg += f"📅 數據發布: {latest_date}\n\n"
    
    for res in fed_results:
        msg += f"• {res['指標']}: {res['數值']} [{res['判定']}]\n"

    msg += "\n🎯 升降息機率預估：\n"
    msg += f"🔴 升息: {prob['升息']}% | 🟡 持平: {prob['持平']}% | 🟢 降息: {prob['降息']}%\n\n"
    
    msg += "======================\n"
    msg += "📈 【全球核心資產報價】\n"
    for name, price in prices.items():
        msg += f"• {name}: {price}\n"
    
    msg += "\n💰 【台股現貨籌碼 (單位:億)】\n"
    for name, net_buy in chips.items():
        icon = "🔴" if isinstance(net_buy, (int, float)) and net_buy > 0 else ("🟢" if isinstance(net_buy, (int, float)) and net_buy < 0 else "⚪")
        msg += f"• {name}: {icon} {net_buy}\n"
        
    msg += "\n(註: 期貨未平倉與融資券因交易所資安限制，暫以現貨為主作為每日動能觀測)"

    # 送出 LINE 訊息
    send_line_message(LINE_ACCESS_TOKEN, LINE_USER_ID, msg)
    print("✅ 執行完畢並成功推播！")
