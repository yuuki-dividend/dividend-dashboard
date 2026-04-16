"""スプレッドシート → stocks.json 自動同期スクリプト

使い方: python3 sync_from_sheets.py
所要時間: 新規銘柄なし→2〜3秒 / 新規銘柄あり→+5秒/銘柄
"""

import csv
import json
import os
import io
import re
import time
import urllib.request
import urllib.parse

SPREADSHEET_ID = "1sy_gEq4kp7RuLiKYl8mIH1UC97H2gZYAl_U_xx9pHcQ"
SHEET_NAME = "MF取込"
RANGE = "A3:J60"

CSV_URL = (
    f"https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}"
    f"/gviz/tq?tqx=out:csv&sheet={urllib.parse.quote(SHEET_NAME)}&range={RANGE}"
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STOCKS_FILE = os.path.join(BASE_DIR, "stocks.json")
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"


def clean_number(s):
    """'1,908' や '51,625円' → float"""
    s = s.replace(",", "").replace("円", "").replace("%", "").strip()
    if not s or s == "-":
        return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


def fetch_sheet_data():
    """スプレッドシートからCSVを取得してパース"""
    print("📡 スプレッドシートからデータ取得中...")
    req = urllib.request.Request(CSV_URL)
    with urllib.request.urlopen(req, timeout=15) as resp:
        text = resp.read().decode("utf-8")

    reader = csv.reader(io.StringIO(text))
    header = next(reader)  # ヘッダー行スキップ

    stocks = []
    for row in reader:
        if len(row) < 5:
            continue
        code_str = row[0].strip()
        if not code_str or not code_str.isdigit():
            continue

        code = int(code_str)
        name = row[1].strip()
        shares = int(clean_number(row[2]))
        buy_price = clean_number(row[3])
        cur_price = clean_number(row[4])

        if shares == 0 and buy_price == 0:
            continue

        # Check for NISA column (column F onwards)
        nisa = "課税"
        for col in row[5:]:
            val = col.strip().upper()
            if val in ("NISA", "つみたてNISA", "成長投資枠", "つみたて投資枠"):
                nisa = "NISA"
                break

        stocks.append({
            "code": code,
            "name": name,
            "shares": shares,
            "buy_price": buy_price,
            "cur_price": cur_price,
            "nisa": nisa,
        })

    return stocks


def fetch_stock_info(code):
    """kabutan.jp / minkabu.jp から配当・セクター・PER・PBR等を取得
    複数ソースでクロスチェックし、配当利回りの整合性を検証"""
    info = {}
    cur_price = 0

    # --- kabutan ---
    url = f"https://kabutan.jp/stock/?code={code}"
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            html = resp.read().decode("utf-8", errors="ignore")

        text_all = re.sub(r"<[^>]+>", " ", html)
        text_all = re.sub(r"\s+", " ", text_all)

        # セクター
        sector_matches = re.findall(
            r'href="[^"]*industry[^"]*"[^>]*>([^<]+)</a>', html
        )
        for sm in sector_matches:
            s = sm.strip()
            if s and s != "一覧を見る" and len(s) < 20:
                info["sector"] = s
                break

        # PER/PBR/利回り
        tables = re.findall(r"<table[^>]*>.*?</table>", html, re.DOTALL)
        for t in tables:
            if "PER" in t and "PBR" in t and "利回り" in t:
                text_t = re.sub(r"<[^>]+>", " ", t)
                text_t = re.sub(r"\s+", " ", text_t).strip()
                bai_vals = re.findall(r"([\d.]+)\s*倍", text_t)
                if len(bai_vals) >= 2:
                    info["per"] = float(bai_vals[0])
                    info["pbr"] = float(bai_vals[1])
                    info["mix_coef"] = round(info["per"] * info["pbr"], 2)
                break

        # 現在値を取得
        yen_vals = re.findall(r">([\d,]+)\s*円", html)
        for yv in yen_vals:
            p = float(yv.replace(",", ""))
            if 10 < p < 500000:
                cur_price = p
                break

        # 配当（予想）
        m = re.search(r"(?:予想配当|配当合計|年間配当)[^\d]*([\d,.]+)\s*円", text_all)
        if m:
            div_str = m.group(1).replace(",", "")
            info["annual_div"] = float(div_str)
            info["mid_div"] = round(float(div_str) / 2, 1)

    except Exception as e:
        print(f"  ⚠️ kabutan取得エラー ({code}): {e}")

    # --- minkabu: 配当額・利回り・配当性向・増配 ---
    try:
        url2 = f"https://minkabu.jp/stock/{code}/dividend"
        req2 = urllib.request.Request(url2, headers={"User-Agent": UA})
        with urllib.request.urlopen(req2, timeout=15) as resp2:
            html2 = resp2.read().decode("utf-8", errors="ignore")
        text2 = re.sub(r'<[^>]+>', '|', html2)
        text2 = re.sub(r'\s+', ' ', text2)

        # 配当利回り
        m_yield = re.search(r'配当利回り\|[^|]*?\|?\s*([\d.]+)\s*%', text2)
        minkabu_yield = float(m_yield.group(1)) if m_yield else 0

        # 年間配当額
        m_div2 = re.search(r'(?:予想(?:一株)?配当|一株配当)\|[^|]*?\|?\s*([\d,.]+)\s*円', text2)
        minkabu_div = float(m_div2.group(1).replace(",", "")) if m_div2 else 0

        # 配当性向
        m = re.search(r'配当性向\|[^%]*?(\d+\.?\d+)%', text2)
        if m:
            info["payout_ratio"] = float(m.group(1))

        # 増配実績
        inc = re.search(r'(\d+)\s*(?:期|年)\s*連続\s*増配', html2)
        if inc:
            info["div_trend"] = f"{inc.group(1)}期連続増配"
        elif "非減配" in html2:
            m_non = re.search(r'(\d+)\s*(?:期|年)\s*連続\s*非減配', html2)
            if m_non:
                info["div_trend"] = f"{m_non.group(1)}期連続非減配"

        # クロスチェック
        if minkabu_yield > 0 and cur_price > 0:
            expected_div = round(cur_price * minkabu_yield / 100, 1)
            kabutan_div = info.get("annual_div", 0)
            if kabutan_div == 0 or abs(kabutan_div - expected_div) / max(expected_div, 1) > 0.20:
                if minkabu_div > 0:
                    info["annual_div"] = minkabu_div
                    info["mid_div"] = round(minkabu_div / 2, 1)
                    print(f"  [{code}] minkabu配当額採用: {minkabu_div}円 (kabutan: {kabutan_div}円)")
                else:
                    info["annual_div"] = expected_div
                    info["mid_div"] = round(expected_div / 2, 1)
                    print(f"  [{code}] 利回り{minkabu_yield}%から逆算: {expected_div}円 (kabutan: {kabutan_div}円)")
        elif minkabu_div > 0 and info.get("annual_div", 0) == 0:
            info["annual_div"] = minkabu_div
            info["mid_div"] = round(minkabu_div / 2, 1)

    except Exception as e:
        print(f"  ⚠️ minkabu取得エラー ({code}): {e}")

    # 利回りが高すぎる場合の警告
    if cur_price > 0 and info.get("annual_div", 0) > 0:
        calc_yield = info["annual_div"] / cur_price * 100
        if calc_yield > 10:
            print(f"  ⚠️ [{code}] 利回り{calc_yield:.1f}%が異常に高い。要確認。")

    return info


def merge_with_existing(new_stocks):
    """既存のstocks.jsonとマージ（配当情報などを保持）"""
    existing = []
    if os.path.exists(STOCKS_FILE):
        with open(STOCKS_FILE, encoding="utf-8") as f:
            existing = json.load(f)

    existing_map = {s["code"]: s for s in existing}
    merged = []
    new_codes = []

    for ns in new_stocks:
        if ns["code"] in existing_map:
            entry = existing_map[ns["code"]].copy()
            entry["shares"] = ns["shares"]
            entry["buy_price"] = ns["buy_price"]
            entry["cur_price"] = ns["cur_price"]
            entry["name"] = ns["name"]
            if ns.get("nisa") == "NISA":
                entry["nisa"] = "NISA"
        else:
            entry = {
                **ns,
                "annual_div": 0,
                "mid_div": 0,
                "mid_month": 12,
                "end_month": 6,
                "nisa": "課税",
                "sector": "",
            }
            new_codes.append(ns["code"])
        merged.append(entry)

    # 新規銘柄 + データ未取得の銘柄の情報を自動取得
    incomplete = [
        s for s in merged
        if s.get("annual_div", 0) == 0 or not s.get("sector")
    ]

    if incomplete:
        print(f"🔍 {len(incomplete)}銘柄のデータを取得中...")
        for stock in incomplete:
            code = stock["code"]
            print(f"  → {code} {stock['name']}...", end=" ")
            info = fetch_stock_info(code)
            if info:
                stock.update(info)
                parts = []
                if "annual_div" in info:
                    parts.append(f"配当{info['annual_div']}円")
                if "sector" in info:
                    parts.append(info["sector"])
                if "per" in info:
                    parts.append(f"PER{info['per']}")
                print("✅ " + ", ".join(parts) if parts else "△ 一部取得")
            else:
                print("❌ 取得失敗")
            time.sleep(1)

    return merged


def main():
    new_stocks = fetch_sheet_data()
    print(f"✅ {len(new_stocks)}銘柄を取得")

    merged = merge_with_existing(new_stocks)

    with open(STOCKS_FILE, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)

    print(f"💾 stocks.json に {len(merged)}銘柄を保存")
    print(f"🌐 http://localhost:8080/ をリロードしてください")


if __name__ == "__main__":
    main()
