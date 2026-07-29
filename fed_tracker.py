import os
import json
import urllib.request
import pandas as pd

class LineFedMonitor:
    def __init__(self):
        # 讀取環境變數中的金鑰
        self.fred_api_key = os.getenv("FRED_API_KEY")
        self.line_token = os.getenv("LINE_ACCESS_TOKEN")
        self.line_user_id = os.getenv("LINE_USER_ID")
        self.base_url = "https://api.stlouisfed.org/fred/series/observations"

    def _get_latest_obs(self, series_id, units="lin"):
        url = f"{self.base_url}?series_id={series_id}&api_key={self.fred_api_key}&file_type=json&sort_order=desc&limit=5&units={units}"
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req) as response:
                data = json.loads(response.read().decode('utf-8'))
                for obs in data.get('observations', []):
                    if obs.get('value', '.') != '.':
                        return float(obs['value']), obs.get('date')
        except Exception as e:
            print(f"⚠️ {series_id} 抓取失敗: {e}")
        return None, None

    def run_analysis_and_notify(self):
        print("🔄 開始抓取 FRED 數據...")
        
        # 抓取指標
        pce_val, pce_date = self._get_latest_obs("PCEPILFE", units="pc1")
        eci_val, _ = self._get_latest_obs("ECIALLCIV", units="pc1")
        jolts_val, _ = self._get_latest_obs("JTSJOL")
        unemp_val, _ = self._get_latest_obs("UNEMPLOY")
        jolts_ratio = round(jolts_val / unemp_val, 2) if (jolts_val and unemp_val) else None
        sahm_val, _ = self._get_latest_obs("SAHMREALTIME")
        nfci_val, _ = self._get_latest_obs("NFCI")
        inf_exp_val, _ = self._get_latest_obs("T5YIE")

        metrics = [
            ("Core PCE", pce_val, 0.25, lambda x: "Hike" if x > 3.2 else ("Cut" if x < 2.3 else "Hold")),
            ("ECI 薪資", eci_val, 0.20, lambda x: "Hike" if x > 4.0 else ("Cut" if x < 2.8 else "Hold")),
            ("JOLTS 求供比", jolts_ratio, 0.15, lambda x: "Hike" if x > 1.50 else ("Cut" if x < 0.80 else "Hold")),
            ("薩姆規則", sahm_val, 0.15, lambda x: "Cut" if x >= 0.50 else "Hold"),
            ("NFCI 金融條件", nfci_val, 0.15, lambda x: "Hike" if x < -0.60 else ("Cut" if x > 0.20 else "Hold")),
            ("5Y 通膨預期", inf_exp_val, 0.10, lambda x: "Hike" if x > 2.70 else ("Cut" if x < 1.80 else "Hold"))
        ]

        hike_score, cut_score, hold_score = 0.0, 0.0, 0.0
        report_lines = [f"📊 【聯準會決策儀表板】\n📅 數據發布: {pce_date}\n"]

        for name, val, weight, eval_fn in metrics:
            if val is not None:
                signal = eval_fn(val)
                if signal == "Hike":
                    hike_score += weight
                    tag = "🔴 升息"
                elif signal == "Cut":
                    cut_score += weight
                    tag = "🟢 降息"
                else:
                    hold_score += weight
                    tag = "🟡 持平"
                report_lines.append(f"• {name}: {val:.2f} [{tag}]")

        tot = hike_score + cut_score + hold_score
        hike_p, hold_p, cut_p = round(hike_score/tot*100, 1), round(hold_score/tot*100, 1), round(cut_score/tot*100, 1)

        report_lines.append(f"\n🎯 升降息機率預估：")
        report_lines.append(f"🔴 升息: {hike_p}% | 🟡 持平: {hold_p}% | 🟢 降息: {cut_p}%")
        
        final_report = "\n".join(report_lines)
        self._send_line(final_report)

    def _send_line(self, text):
        if not (self.line_token and self.line_user_id):
            print("❌ 缺少 LINE 金鑰，無法發送")
            return
        
        url = "https://api.line.me/v2/bot/message/push"
        payload = json.dumps({
            "to": self.line_user_id,
            "messages": [{"type": "text", "text": text}]
        }).encode('utf-8')

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.line_token}"
        }
        try:
            req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
            with urllib.request.urlopen(req) as resp:
                print("✅ LINE 訊息推播成功！請檢查手機。")
        except Exception as e:
            print(f"❌ LINE 發送失敗: {e}")

if __name__ == "__main__":
    monitor = LineFedMonitor()
    monitor.run_analysis_and_notify()
