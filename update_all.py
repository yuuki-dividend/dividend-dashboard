#!/usr/bin/env python3
"""高配当株ダッシュボード 一括更新スクリプト

Phase 1: stocks.json の株価・配当・PER/PBR更新 (minkabuから取得)
Phase 2: スクリーニング実行 (run_screening.py)
Phase 3: バリデーション (全データの整合性チェック)

使い方:
  python3 update_all.py                  # フル更新
  python3 update_all.py --skip-screening # stocks.jsonのみ更新+検証
  python3 update_all.py --validate-only  # 検証のみ (データ取得なし)
"""

import argparse
import json
import os
import re
import shutil
import sys
import time
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STOCKS_FILE = os.path.join(BASE_DIR, "stocks.json")
SCREENING_FILE = os.path.join(BASE_DIR, "screening_data.json")
DOCS_DIR = os.path.join(BASE_DIR, "docs")

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
ETF_CODES = {1343, 1489}


def ts():
    """タイムスタンプ文字列"""
    return datetime.now().strftime("%H:%M:%S")


def fetch_url(url, retries=2):
    """URLからHTMLを取得 (リトライ付き)"""
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return resp.read().decode("utf-8", errors="replace")
        except Exception as e:
            if attempt < retries:
                time.sleep(1)
                continue
            print(f"  [WARN] {url} 取得失敗: {e}")
            return None


def strip_html(html):
    """HTMLタグ除去 → パイプ区切りテキスト化"""
    text = re.sub(r'<[^>]+>', '|', html)
    text = re.sub(r'\s+', ' ', text)
    return text


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"  [{ts()}] {os.path.basename(path)} 保存完了")


# ============================================================
# Phase 1: stocks.json 更新
# ============================================================
def _fetch_one_stock(stock):
    """1銘柄のデータを取得（並列実行用）"""
    code = stock["code"]
    is_etf = code in ETF_CODES
    result = {"code": code, "errors": []}

    # --- 株価取得 (minkabu JSON-LD) ---
    main_html = fetch_url(f"https://minkabu.jp/stock/{code}")
    price = None
    per = None
    pbr = None

    if main_html:
        m = re.search(r'"offers":\s*\{[^}]*"price":\s*"?([\d,.]+)', main_html)
        if m:
            price = float(m.group(1).replace(",", ""))
            result["cur_price"] = price

        main_text = strip_html(main_html)
        m_per = re.search(r'PER\|[^倍]*?([\d,.]+)\s*倍', main_text)
        if m_per:
            per = float(m_per.group(1).replace(",", ""))
            result["per"] = per

        m_pbr = re.search(r'PBR\|[^倍]*?([\d,.]+)\s*倍', main_text)
        if m_pbr:
            pbr = float(m_pbr.group(1).replace(",", ""))
            result["pbr"] = pbr

        if per and pbr:
            result["mix_coef"] = round(per * pbr, 2)
    else:
        result["errors"].append(f"{code}: メインページ取得失敗")

    # --- 配当利回り取得 (ETFはスキップ) ---
    if not is_etf:
        div_html = fetch_url(f"https://minkabu.jp/stock/{code}/dividend")
        if div_html:
            div_text = strip_html(div_html)
            m_yield = re.search(r'配当利回り\|[^|]*?\|?\s*([\d.]+)\s*%', div_text)
            if m_yield and price:
                yld = float(m_yield.group(1))
                annual_div = round(price * yld / 100, 1)
                result["annual_div"] = annual_div
                result["mid_div"] = round(annual_div / 2, 1)
            elif not m_yield:
                result["errors"].append(f"{code}: 配当利回りパース失敗")
            elif not price:
                result["errors"].append(f"{code}: 株価なしのため配当額計算スキップ")
        else:
            result["errors"].append(f"{code}: 配当ページ取得失敗")

    return result


def update_stocks():
    print(f"\n{'='*60}")
    print(f"  Phase 1: stocks.json 更新 ({ts()})")
    print(f"{'='*60}")

    stocks = load_json(STOCKS_FILE)
    total = len(stocks)
    updated_price = 0
    updated_div = 0
    errors = []

    # 並列でデータ取得（5並列）
    print(f"  [{ts()}] {total}銘柄を5並列で取得開始...")
    stock_map = {s["code"]: s for s in stocks}
    results = {}

    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(_fetch_one_stock, s): s["code"] for s in stocks}
        done_count = 0
        for future in as_completed(futures):
            done_count += 1
            code = futures[future]
            try:
                res = future.result()
                results[code] = res
            except Exception as e:
                results[code] = {"code": code, "errors": [f"{code}: 例外 {e}"]}
            # 10銘柄ごとに進捗表示
            if done_count % 10 == 0 or done_count == total:
                print(f"  [{ts()}] {done_count}/{total} 完了")

    # 結果をstocksに反映
    for stock in stocks:
        code = stock["code"]
        res = results.get(code, {})
        is_etf = code in ETF_CODES

        if "cur_price" in res:
            stock["cur_price"] = res["cur_price"]
            updated_price += 1
        if "per" in res:
            stock["per"] = res["per"]
        if "pbr" in res:
            stock["pbr"] = res["pbr"]
        if "mix_coef" in res:
            stock["mix_coef"] = res["mix_coef"]
        if "annual_div" in res:
            stock["annual_div"] = res["annual_div"]
            stock["mid_div"] = res["mid_div"]
            updated_div += 1

        errors.extend(res.get("errors", []))

        # ログ出力
        parts = []
        if "cur_price" in res:
            parts.append(f"¥{res['cur_price']:,.0f}")
        if not is_etf and "annual_div" in res and res["cur_price"]:
            yld_calc = res["annual_div"] / res["cur_price"] * 100
            parts.append(f"配当{res['annual_div']}円({yld_calc:.2f}%)")
        if "per" in res:
            parts.append(f"PER{res['per']}")
        print(f"  {code} {stock['name']} -> {', '.join(parts) if parts else 'SKIP'}")

    save_json(STOCKS_FILE, stocks)

    print(f"\n  --- Phase 1 結果 ---")
    print(f"  株価更新: {updated_price}/{total}")
    print(f"  配当更新: {updated_div}/{total - len(ETF_CODES)}")
    if errors:
        print(f"  エラー ({len(errors)}件):")
        for e in errors:
            print(f"    - {e}")

    return stocks


# ============================================================
# Phase 2: スクリーニング実行
# ============================================================
def run_screening():
    print(f"\n{'='*60}")
    print(f"  Phase 2: スクリーニング実行 ({ts()})")
    print(f"{'='*60}")
    print(f"  [{ts()}] run_screening_with_growth() 開始...")
    print(f"  (所要時間: 約10-15分)")

    sys.path.insert(0, BASE_DIR)
    from run_screening import run_screening_with_growth
    run_screening_with_growth()

    print(f"  [{ts()}] スクリーニング完了")


# ============================================================
# Phase 3: バリデーション
# ============================================================
def validate():
    print(f"\n{'='*60}")
    print(f"  Phase 3: バリデーション ({ts()})")
    print(f"{'='*60}")

    issues = []
    warnings = []

    # --- stocks.json ---
    stocks = load_json(STOCKS_FILE)
    total = len(stocks)
    non_etf = [s for s in stocks if s["code"] not in ETF_CODES]
    non_etf_count = len(non_etf)

    price_zero = [s for s in non_etf if s.get("cur_price", 0) == 0]
    div_zero = [s for s in non_etf if s.get("annual_div", 0) == 0]
    has_per = [s for s in non_etf if s.get("per", 0) > 0]

    # 利回り計算
    yields = []
    for s in non_etf:
        p = s.get("cur_price", 0)
        d = s.get("annual_div", 0)
        if p > 0 and d > 0:
            yields.append(d / p * 100)

    avg_yield = sum(yields) / len(yields) if yields else 0
    min_yield = min(yields) if yields else 0
    max_yield = max(yields) if yields else 0

    # 利回り異常値チェック
    outliers = []
    for s in non_etf:
        p = s.get("cur_price", 0)
        d = s.get("annual_div", 0)
        if p > 0 and d > 0:
            y = d / p * 100
            if y < 0.5 or y > 15:
                outliers.append(f"{s['code']} {s['name']}: {y:.2f}%")

    if price_zero:
        issues.append(f"{len(price_zero)} stocks with cur_price = 0: {[s['code'] for s in price_zero]}")
    if div_zero:
        issues.append(f"{len(div_zero)} stocks with annual_div = 0: {[s['code'] for s in div_zero]}")
    if avg_yield < 1 or avg_yield > 8:
        issues.append(f"Average yield {avg_yield:.2f}% is outside 1-8% range")
    if outliers:
        warnings.append(f"Yield outliers: {outliers}")

    print(f"\n=== VALIDATION RESULTS ===")
    print(f"stocks.json: {total} stocks")
    print(f"  Price OK: {non_etf_count - len(price_zero)}/{non_etf_count} (ETF skip: {len(ETF_CODES)})")
    print(f"  Dividend OK: {non_etf_count - len(div_zero)}/{non_etf_count}")
    print(f"  Avg yield: {avg_yield:.2f}%")
    if yields:
        print(f"  Yield range: {min_yield:.2f}% ~ {max_yield:.2f}%")
    print(f"  PER available: {len(has_per)}/{non_etf_count}")

    # --- screening_data.json ---
    if os.path.exists(SCREENING_FILE):
        sd = load_json(SCREENING_FILE)

        hdr = sd.get("high_dividend_ranking", [])
        hdr_per_zero = sum(1 for s in hdr if s.get("per", 0) == 0)
        hdr_mix_zero = sum(1 for s in hdr if s.get("mix_coef", 0) == 0)

        gpr = sd.get("growth_potential_ranking", [])
        gpr_fy_zero = sum(1 for s in gpr if s.get("future_yield_5y", 0) == 0)
        gpr_price_zero = sum(1 for s in gpr if s.get("price", 0) == 0)

        # 成長ポテンシャル: 現在利回り・5年後予想の異常値チェック
        gpr_yield_over20 = [s for s in gpr if s.get("current_yield", 0) > 20]
        gpr_future_over50 = [s for s in gpr if s.get("future_yield_5y", 0) > 50]
        gpr_yield_zero = [s for s in gpr if s.get("current_yield", 0) <= 0]
        gpr_future_negative = [s for s in gpr if s.get("future_yield_5y", 0) < 0]

        print(f"\nscreening_data.json:")
        print(f"  注目銘柄: {len(hdr)} stocks, PER: {len(hdr)-hdr_per_zero}/{len(hdr)}, mix_coef: {len(hdr)-hdr_mix_zero}/{len(hdr)}")
        print(f"  成長ポテンシャル: {len(gpr)} stocks, future_yield_0%: {gpr_fy_zero}, price_0: {gpr_price_zero}")
        print(f"  成長ポテンシャル: 現在利回り異常(>20%): {len(gpr_yield_over20)}, 5年後予想異常(>50%): {len(gpr_future_over50)}")

        if hdr_per_zero > len(hdr) * 0.3:
            issues.append(f"注目銘柄: {hdr_per_zero}/{len(hdr)} stocks with PER=0")
        if hdr_mix_zero > len(hdr) * 0.3:
            issues.append(f"注目銘柄: {hdr_mix_zero}/{len(hdr)} stocks with mix_coef=0")
        if gpr_fy_zero > len(gpr) * 0.3:
            issues.append(f"成長ポテンシャル: {gpr_fy_zero}/{len(gpr)} stocks with future_yield_5y=0")
        if gpr_yield_over20:
            names = ', '.join(f"{s['code']}" for s in gpr_yield_over20[:5])
            issues.append(f"成長ポテンシャル: 現在利回り>20%が{len(gpr_yield_over20)}件 ({names})")
        if gpr_future_over50:
            names = ', '.join(f"{s['code']}" for s in gpr_future_over50[:5])
            issues.append(f"成長ポテンシャル: 5年後予想>50%が{len(gpr_future_over50)}件 ({names})")
        if gpr_yield_zero:
            names = ', '.join(f"{s['code']}" for s in gpr_yield_zero[:5])
            issues.append(f"成長ポテンシャル: 現在利回り0%以下が{len(gpr_yield_zero)}件 ({names})")
        if gpr_future_negative:
            names = ', '.join(f"{s['code']}" for s in gpr_future_negative[:5])
            issues.append(f"成長ポテンシャル: 5年後予想がマイナスが{len(gpr_future_negative)}件 ({names})")
    else:
        print(f"\nscreening_data.json: ファイルなし (--skip-screening時は正常)")

    # --- 最終判定 ---
    print()
    if warnings:
        for w in warnings:
            print(f"  [WARN] {w}")
    if issues:
        print(f"  VALIDATION FAILED")
        for issue in issues:
            print(f"  - {issue}")
        print(f"  Fix these before using the dashboard!")
        return False
    else:
        print(f"  ALL CHECKS PASSED")
        return True


# ============================================================
# Phase 4: docs/ へコピー (GitHub Pages用)
# ============================================================
def copy_to_docs():
    print(f"\n{'='*60}")
    print(f"  Phase 4: docs/ へコピー ({ts()})")
    print(f"{'='*60}")

    if not os.path.exists(DOCS_DIR):
        print(f"  [WARN] docs/ ディレクトリが存在しません。スキップします。")
        return

    # Copy public data files (NOT stocks.json - personal data)
    files_to_copy = [
        ("screening_data.json", "screening_data.json"),
        ("all_stocks.json", "all_stocks.json"),
        ("index.html", "index.html"),
        ("icon-192.svg", "icon-192.svg"),
        ("icon-512.svg", "icon-512.svg"),
        ("icon-maskable.svg", "icon-maskable.svg"),
    ]

    copied = 0
    for src_name, dst_name in files_to_copy:
        src = os.path.join(BASE_DIR, src_name)
        dst = os.path.join(DOCS_DIR, dst_name)
        if os.path.exists(src):
            shutil.copy2(src, dst)
            copied += 1
            print(f"  {src_name} -> docs/{dst_name}")
        else:
            print(f"  [SKIP] {src_name} が見つかりません")

    print(f"\n  {copied}ファイルをdocs/にコピーしました")
    print(f"  ※ stocks.json はコピーしていません（個人データ）")
    print(f"  ※ manifest.json, sw.js はdocs/専用版を使用")
    print(f"  デプロイするには: git add docs/ && git commit && git push")


# ============================================================
# メイン
# ============================================================
def main():
    parser = argparse.ArgumentParser(description="高配当株ダッシュボード一括更新")
    parser.add_argument("--skip-screening", action="store_true",
                        help="スクリーニングをスキップ (株価更新+検証のみ)")
    parser.add_argument("--validate-only", action="store_true",
                        help="検証のみ実行 (データ取得なし)")
    args = parser.parse_args()

    start = time.time()
    print(f"{'='*60}")
    print(f"  高配当株ダッシュボード 一括更新")
    print(f"  開始: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}")

    if args.validate_only:
        print(f"  モード: 検証のみ")
        ok = validate()
    elif args.skip_screening:
        print(f"  モード: 株価更新 + 検証 (スクリーニングスキップ)")
        update_stocks()
        ok = validate()
    else:
        print(f"  モード: フル更新 (株価 + スクリーニング + 検証)")
        update_stocks()
        run_screening()
        ok = validate()

    # バリデーション成功時、docs/ に自動コピー
    if ok and os.path.exists(DOCS_DIR):
        copy_to_docs()

    elapsed = time.time() - start
    minutes = int(elapsed // 60)
    seconds = int(elapsed % 60)
    print(f"\n  完了: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} (所要時間: {minutes}分{seconds}秒)")

    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
