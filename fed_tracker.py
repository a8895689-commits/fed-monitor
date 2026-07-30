import os
import urllib.request
import json
import pandas as pd
import yfinance as yf
import requests
import re

class LiveFedPredictor:
    """主題一：聯準會利率決策預測模組 (串接 FRED API)"""
    def __init__(self, api_key):
        self.api_key = api_key
        self.base_url = "https://api.stlouisfed.org/fred/series/observations"

    def _get_latest_obs(self, series_id, units="lin"):
        """抓取最新『兩筆』有效資料，以計算漲跌幅"""
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
        # 指標資料庫 (名稱, FRED代碼, 單位, 權重, 判定邏輯, 門檻條件說明)
        metrics = [
            ("Core PCE", "PCEPILFE", "pc1", 0.25, 
             lambda x: "Hike" if x > 3.2 else ("Cut" if x < 2.3 else "Hold"), 
             "升>3.2 / 降<2.3"),
            ("ECI 薪資", "ECIALLCIV", "pc1", 0.20, 
             lambda x: "Hike" if x > 4.0 else ("Cut" if x < 2.8 else "Hold"), 
             "升>4.0 / 降<2.8"),
            ("JOLTS 求供比", "JOLTS_RATIO", "lin", 0.15, 
             lambda x: "Hike" if x > 1.2 else ("Cut" if x < 0.8 else "Hold"), 
             "升>1.2 / 降<0.8"),
            ("薩姆規則", "SAHMREALTIME", "lin", 0.15, 
             lambda x: "Cut" if x >= 0.50 else "Hold", 
             "降≥0.50"),
            ("NFCI 金融條件", "NFCI", "lin", 0.15, 
             lambda x: "Hike" if x < -0.7 else ("Cut" if x > 0.5 else "Hold"), 
             "升<-0.7 / 降>0.5"),
            ("5Y 通膨預期", "T5YIE", "lin", 0.10, 
             lambda x: "Hike" if x > 2.5 else ("Cut" if x < 2.0 else "Hold"), 
             "升>2.5 / 降<2.0")
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
                    hike_score += weight
                    sig_txt = "🔴 升息"
                elif signal == "Cut": 
                    cut_score += weight
                    sig_txt = "🟢 降息"
                else: 
                    hold_score += weight
                    sig_txt = "🟡 持平"
                    
                results.append({
                    "指標": name, 
                    "數值": f"{val:.2f}{diff_str}", 
                    "判定": sig_txt,
                    "門檻": condition_str
                })
            else:
                results.append({
                    "指標": name, 
                    "數值": "N/A", 
                    "判定": "❓ 缺資料",
                    "門檻": condition_str
                })

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
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        }

    def fetch_yfinance_prices(self):
        """抓取美股、大盤指數與匯率 (含漲跌幅計算)"""
        tickers = {
            "台股大盤": "^TWII",
            "費半指數": "^SOX",
            "輝達 NVDA": "NVDA",
            "台積電 ADR": "TSM",
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

    def fetch_thirdparty_margin(self):
        """多重管道抓取融資融券：放棄TWSE，改用 鉅亨網 (Anue) / 嗨投資 (HiStock) / 玩股網 (WantGoo) 備援"""
        # 管道 1：鉅亨網 (Anue) API
        try:
            url = "https://api.cnyes.com/media/api/v1/market/tw/margin"
            res = requests.get(url, headers=self.headers, timeout=5)
            if res.status_code == 200:
                data = res.json()
                items = data.get('items', [])
                if items:
                    latest = items[0]
                    margin_bal = float(latest.get('marginLongBalance', 0)) / 100000
                    margin_diff = float(latest.get('marginLongChange', 0)) / 100000
                    short_bal = float(latest.get('marginShortBalance', 0)) / 10000
                    short_diff = float(latest.get('marginShortChange', 0)) / 10000
                    
                    sign_m = "+" if margin_diff > 0 else ""
                    sign_s = "+" if short_diff > 0 else ""
                    return {
                        "融資餘額(億)": f"{margin_bal:.2f} ({sign_m}{margin_diff:.2f})",
                        "融券餘額(萬張)": f"{short_bal:.2f} ({sign_s}{short_diff:.2f})",
                        "融資維持率": "N/A (官方未直接提供)"
                    }
        except Exception:
            pass

        # 管道 2：嗨投資 (HiStock) 網頁解析
        try:
            url = "https://histock.tw/stock/margin.aspx"
            res = requests.get(url, headers=self.headers, timeout=5)
            if res.status_code == 200:
                html = res.text
                m_bal = re.search(r'融資餘額.*?([\d,]+\.?\d*)', html)
                m_diff = re.search(r'融資增減.*?([+-]?[\d,]+\.?\d*)', html)
                s_bal = re.search(r'融券餘額.*?([\d,]+\.?\d*)', html)
                s_diff = re.search(r'融券增減.*?([+-]?[\d,]+\.?\d*)', html)
                
                if m_bal and m_diff:
                    mb = float(m_bal.group(1).replace(',', ''))
                    md_str = m_diff.group(1).replace(',', '')
                    md = float(md_str)
                    
                    sb = float(s_bal.group(1).replace(',', '')) if s_bal else 0.0
                    sd_str = s_diff.group(1).replace(',', '') if s_diff else "0"
                    sd = float(sd_str)
                    
                    sign_m = "+" if md > 0 and not md_str.startswith('+') else ""
                    sign_s = "+" if sd > 0 and not sd_str.startswith('+') else ""
                    return {
                        "融資餘額(億)": f"{mb:.2f} ({sign_m}{md_str})",
                        "融券餘額(萬張)": f"{sd:.2f} ({sign_s}{sd_str})",
                        "融資維持率": "N/A (官方未直接提供)"
                    }
        except Exception:
            pass

        # 管道 3：玩股網 (WantGoo) API 備援
        try:
            url = "https://www.wantgoo.com/investor/margin-trading/total-data"
            res = requests.get(url, headers=self.headers, timeout=5)
            if res.status_code == 200:
                data = res.json()
                if isinstance(data, list) and len(data) > 0:
                    latest = data[0]
                    mb = float(latest.get('marginBalance', 0))
                    md = float(latest.get('marginChange', 0))
                    sb = float(latest.get('shortBalance', 0))
                    sd = float(latest.get('shortChange', 0))
                    
                    sign_m = "+" if md > 0 else ""
                    sign_s = "+" if sd > 0 else ""
                    return {
                        "融資餘額(億)": f"{mb:.2f} ({sign_m}{md:.2f})",
                        "融券餘額(萬張)": f"{sb:.2f} ({sign_s}{sd:.2f})",
                        "融資維持率": "N/A (官方未直接提供)"
                    }
        except Exception:
            pass

        return {
            "融資餘額(億)": "N/A",
            "融券餘額(萬張)": "N/A",
            "融資維持率": "N/A (官方未直接提供)"
        }

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
    market_bot = DailyMarketTracker()
    prices = market_bot.fetch_yfinance_prices()
    
    # 2.5 抓取三大法人與第三方融資融券 (鉅亨網/嗨投資/玩股網多重備援)
    chips = market_bot.fetch_twse_institutional()
    margin_data = market_bot.fetch_thirdparty_margin()
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
        
    msg += "\n(註: 期貨未平倉因資安限制暫以現貨為主；融資維持率官方不直接公佈。)"

    # 送出 LINE 訊息
    send_line_message(LINE_ACCESS_TOKEN, LINE_USER_ID, msg)
    print("✅ 執行完畢並成功推播！")
