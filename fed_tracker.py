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
        metrics = [
            ("Core PCE通膨", "PCEPILFE", "pc1", 0.25, lambda x: "Hike" if x > 3.2 else ("Cut" if x < 2.3 else "Hold")),
            ("ECI就業成本", "ECIALLCIV", "pc1", 0.20, lambda x: "Hike" if x > 4.0 else ("Cut" if x < 2.8 else "Hold")),
            ("薩姆衰退規則", "SAHMREALTIME", "lin", 0.15, lambda x: "Cut" if x >= 0.50 else "Hold")
        ]
        
        results, hike_score, cut_score, hold_score = [], 0.0, 0.0, 0.0
        for name, sid, unit, weight, eval_fn in metrics:
            val, date = self._get_latest_obs(sid, units=unit)
            if val is not None:
                signal = eval_fn(val)
                if signal == "Hike": hike_score += weight; sig_txt = "🔴 升息"
                elif signal == "Cut": cut_score += weight; sig_txt = "🟢 降息"
                else: hold_score += weight; sig_txt = "🟡 持平"
                results.append({"指標": name, "數值": f"{val:.2f}", "判定": sig_txt})
            else:
                results.append({"指標": name, "數值": "N/A", "判定": "❓ 缺資料"})

        total = hike_score + cut_score + hold_score
        prob = {
            "升息": round((hike_score / total) * 100, 1) if total > 0 else 0,
            "降息": round((cut_score / total) * 100, 1) if total > 0 else 0,
            "持平": round((hold_score / total) * 100, 1) if total > 0 else 0
        }
        return results, prob


class DailyMarketTracker:
    """主題二：台美股價與台股籌碼自動化模組"""
    def __init__(self):
        self.headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}

    def fetch_yfinance_prices(self):
        """抓取美股與大盤指數"""
        tickers = {
            "台股大盤": "^TWII",
            "費半指數": "^SOX",
            "輝達 NVDA": "NVDA",
            "台積電 ADR": "TSM"
        }
        prices = {}
        for name, symbol in tickers.items():
            try:
                # 抓取最近一天的收盤價
                stock = yf.Ticker(symbol)
                hist = stock.history(period="1d")
                if not hist.empty:
                    prices[name] = round(hist['Close'].iloc[-1], 2)
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
                    # 將字串中的逗號去除並轉為億元
                    net_buy = round(int(row[3].replace(',', '')) / 100000000, 2)
                    if "外資及陸資" in name: chips["外資及陸資"] = net_buy
                    elif "投信" in name: chips["投信"] = net_buy
                    elif "自營商(自行買賣)" in name: chips["自營商(自行)"] = net_buy
                    elif "自營商(避險)" in name: chips["自營商(避險)"] = net_buy
        except Exception:
            chips = {k: "N/A (遭證交所阻擋)" for k in chips}
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
    fed_results, prob = fed_bot.analyze()
    max_policy = max(prob, key=prob.get)

    # 2. 抓取市場價格與籌碼數據
    market_bot = DailyMarketTracker()
    prices = market_bot.fetch_yfinance_prices()
    chips = market_bot.fetch_twse_institutional()

    # 3. 組合 LINE 訊息
    msg = "🌍 全球與台股總經儀表板 🌍\n"
    msg += "=" * 20 + "\n"
    msg += "🇺🇸 【主題一：聯準會預測】\n"
    msg += f"🔴 升息機率：{prob['升息機率']}%\n"
    msg += f"🟡 持平機率：{prob['按兵不動機率']}%\n"
    msg += f"🟢 降息機率：{prob['降息機率']}%\n"
    msg += f"🎯 結論最支持：【{max_policy}】\n\n"
    msg += "[微觀數據追蹤]\n"
    for _, row in df_fed.iterrows():
        msg += f"• {row['指標名稱']}: {row['最新數值']} ({row['燈號判定'].split(' ')[0]})\n"

    msg += "📈 【全球核心資產報價】\n"
    for name, price in prices.items():
        msg += f"• {name}: {price}\n"
    
    msg += "\n💰 【台股現貨籌碼 (單位:億)】\n"
    for name, net_buy in chips.items():
        # 自動標示買賣超的表情符號
        icon = "🔴" if isinstance(net_buy, (int, float)) and net_buy > 0 else ("🟢" if isinstance(net_buy, (int, float)) and net_buy < 0 else "⚪")
        msg += f"• {name}: {icon} {net_buy}\n"
        
    msg += "\n(註: 期貨未平倉與融資券因交易所資安限制，暫以現貨為主作為每日動能觀測)"

    send_line_message(LINE_ACCESS_TOKEN, LINE_USER_ID, msg)
    print("✅ 執行完畢並成功推播！")
