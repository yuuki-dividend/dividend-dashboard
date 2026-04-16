"""毎日の終値確定後に株価を自動更新するスクリプト
東証の終値確定（15:30頃）後、16:00-16:30に実行想定

更新対象:
  - stocks.json: ポートフォリオ銘柄の cur_price
  - screening_data.json: スクリーニング銘柄の price
"""

import json
import os
import re
import time
import urllib.request
import urllib.error
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STOCKS_FILE = os.path.join(BASE_DIR, "stocks.json")
SCREENING_FILE = os.path.join(BASE_DIR, "screening_data.json")
LOG_FILE = os.path.join(BASE_DIR, "price_update.log")

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"

# 取得済み価格キャッシュ（同一実行内で重複取得を防止）
_price_cache = {}


def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def fetch_price_stooq(code):
    """stooq.comのCSV APIから終値を取得（メインソース）"""
    url = f"https://stooq.com/q/l/?s={code}.jp&f=sd2t2ohlcv&h&e=csv"
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            text = resp.read().decode("utf-8").strip()
        lines = text.split("\n")
        if len(lines) >= 2:
            vals = lines[1].split(",")
            if len(vals) >= 7 and vals[6].replace(".", "").replace("-", "").isdigit():
                close = float(vals[6])
                if close > 0:
                    return close
    except Exception as e:
        log(f"  stooq error for {code}: {e}")
    return None


def fetch_price_kabutan(code):
    """kabutan.jpから株価を取得（フォールバック）"""
    url = f"https://kabutan.jp/stock/?code={code}"
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            html = resp.read().decode("utf-8", errors="ignore")
        m = re.search(r'class="[^"]*kabuka[^"]*"[^>]*>([\d,]+)', html)
        if m:
            return float(m.group(1).replace(",", ""))
    except Exception as e:
        log(f"  kabutan error for {code}: {e}")
    return None


# PER/PBRキャッシュ
_perpbr_cache = {}


def fetch_per_pbr_kabutan(code):
    """kabutan.jpからPER/PBRを取得"""
    if code in _perpbr_cache:
        return _perpbr_cache[code]

    url = f"https://kabutan.jp/stock/?code={code}"
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            html = resp.read().decode("utf-8", errors="ignore")
        tables = re.findall(r"<table[^>]*>.*?</table>", html, re.DOTALL)
        for t in tables:
            if "PER" in t and "PBR" in t and "利回り" in t:
                text = re.sub(r"<[^>]+>", " ", t)
                text = re.sub(r"\s+", " ", text).strip()
                m = re.search(r"PER\s+PBR.*?([\d.]+|－)\s*倍\s+([\d.]+|－)\s*倍", text)
                if m:
                    per_str, pbr_str = m.group(1), m.group(2)
                    per = float(per_str) if per_str != "－" else None
                    pbr = float(pbr_str) if pbr_str != "－" else None
                    if per or pbr:
                        _perpbr_cache[code] = (per, pbr)
                        return per, pbr
    except Exception as e:
        log(f"  kabutan PER/PBR error for {code}: {e}")
    _perpbr_cache[code] = (None, None)
    return None, None


# 配当性向・増配キャッシュ
_dividend_info_cache = {}


def fetch_dividend_info_minkabu(code):
    """minkabu.jpから配当性向と増配実績を取得"""
    if code in _dividend_info_cache:
        return _dividend_info_cache[code]

    url = f"https://minkabu.jp/stock/{code}/dividend"
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    payout_ratio = None
    div_trend = ""
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            html = resp.read().decode("utf-8", errors="ignore")

        text = re.sub(r'<[^>]+>', '|', html)
        text = re.sub(r'\s+', ' ', text)

        # 配当性向: "配当性向| |XX.XX%" パターン
        m = re.search(r'配当性向\|[^%]*?(\d+\.?\d+)%', text)
        if m:
            payout_ratio = float(m.group(1))

        # 増配実績: 年度別配当データから判定
        # 配当推移テーブル: "2025年3月期| |46.25%| |108.09円"
        div_entries = re.findall(
            r'(\d{4})年\d+月期\|[^|]*\|[\d.]+%\|[^|]*\|([\d.]+)円',
            text
        )
        if div_entries:
            sorted_entries = sorted(div_entries, key=lambda x: int(x[0]))
            consecutive = 0
            prev_amt = 0
            for year, amount in sorted_entries:
                amt = float(amount)
                if prev_amt > 0:
                    if amt >= prev_amt:
                        consecutive += 1
                    else:
                        consecutive = 0
                prev_amt = amt
            if consecutive >= 2:
                div_trend = f"{consecutive}期連続増配"
            elif consecutive >= 0 and len(sorted_entries) >= 3:
                div_trend = "非減配"

        if not div_trend:
            inc_match = re.search(r'(\d+)\s*(?:期|年)\s*連続\s*増配', html)
            if inc_match:
                div_trend = f"{inc_match.group(1)}期連続増配"
            elif '非減配' in html or '減配なし' in html:
                div_trend = "非減配"

    except Exception as e:
        log(f"  minkabu dividend error for {code}: {e}")

    result = (payout_ratio, div_trend)
    _dividend_info_cache[code] = result
    return result


def fetch_price(code):
    """株価を取得（キャッシュ付き、複数ソース）"""
    if code in _price_cache:
        return _price_cache[code]

    for fetcher in [fetch_price_stooq, fetch_price_kabutan]:
        price = fetcher(code)
        if price and price > 0:
            _price_cache[code] = price
            return price
        time.sleep(0.3)

    return None


def update_stocks_json():
    """stocks.jsonのcur_priceを更新"""
    if not os.path.exists(STOCKS_FILE):
        log("stocks.json not found, skipping")
        return 0, 0

    with open(STOCKS_FILE, encoding="utf-8") as f:
        stocks = json.load(f)

    updated = 0
    failed = 0
    for stock in stocks:
        code = stock["code"]
        price = fetch_price(code)
        if price:
            old_price = stock.get("cur_price", 0)
            stock["cur_price"] = price
            log(f"  {code} {stock['name']}: {old_price} -> {price}")
            updated += 1
        else:
            log(f"  {code} {stock['name']}: FAILED")
            failed += 1
        # PER/PBR取得
        per, pbr = fetch_per_pbr_kabutan(code)
        if per:
            stock["per"] = per
        if pbr:
            stock["pbr"] = pbr
        if per and pbr:
            stock["mix_coef"] = round(per * pbr, 2)
        # 配当性向・増配実績
        payout, trend = fetch_dividend_info_minkabu(code)
        if payout is not None:
            stock["payout_ratio"] = payout
        if trend:
            stock["div_trend"] = trend
        time.sleep(0.5)

    with open(STOCKS_FILE, "w", encoding="utf-8") as f:
        json.dump(stocks, f, ensure_ascii=False, indent=2)

    return updated, failed


def update_screening_json():
    """screening_data.jsonの株価を更新"""
    if not os.path.exists(SCREENING_FILE):
        log("screening_data.json not found, skipping")
        return 0, 0

    with open(SCREENING_FILE, encoding="utf-8") as f:
        data = json.load(f)

    updated = 0
    failed = 0

    def update_stock_data(stock):
        nonlocal updated, failed
        code = stock.get("code")
        if not code:
            return
        price = fetch_price(code)
        if price:
            stock["price"] = price
            updated += 1
        else:
            failed += 1
        per, pbr = fetch_per_pbr_kabutan(code)
        if per and pbr:
            stock["per"] = per
            stock["pbr"] = pbr
            stock["mix_coef"] = round(per * pbr, 2)
        payout, trend = fetch_dividend_info_minkabu(code)
        if payout is not None:
            stock["payout_ratio"] = payout
        if trend:
            stock["div_trend"] = trend
        time.sleep(0.5)

    # high_dividend_ranking
    for stock in data.get("high_dividend_ranking", []):
        update_stock_data(stock)

    # buy_timing_ranking
    for stock in data.get("buy_timing_ranking", []):
        update_stock_data(stock)

    # sector_recommendations
    for sector, stocks_list in data.get("sector_recommendations", {}).items():
        if not isinstance(stocks_list, list):
            continue
        for stock in stocks_list:
            update_stock_data(stock)

    data["price_date"] = datetime.now().strftime("%Y-%m-%d")

    with open(SCREENING_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    return updated, failed


def main():
    log("=" * 50)
    log("株価自動更新開始")
    log("=" * 50)

    start = time.time()

    log("[1/2] stocks.json 更新中...")
    s_ok, s_ng = update_stocks_json()
    log(f"  完了: {s_ok}件更新, {s_ng}件失敗")

    log("[2/2] screening_data.json 更新中...")
    r_ok, r_ng = update_screening_json()
    log(f"  完了: {r_ok}件更新, {r_ng}件失敗")

    elapsed = time.time() - start
    log(f"全完了: 合計{s_ok + r_ok}件更新, {s_ng + r_ng}件失敗, {elapsed:.1f}秒")
    log("")


if __name__ == "__main__":
    main()
