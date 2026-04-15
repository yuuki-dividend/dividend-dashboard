"""リベ大流 高配当株スクリーニング（強化版）

Yahoo!ファイナンス + 投資の森 から高配当銘柄候補を取得し、
kabutan.jp + minkabu.jp から詳細財務データを収集、
リベ大の基準で包括的にスコアリングして screening_data.json を更新する。

リベ大スクリーニング基準（IR BANK確認項目を再現）:
  1. 配当利回り 3.75% 以上
  2. 配当性向 70% 以下（利益の余力あり）→ 100%超は即失格
  3. 連続増配 or 非減配の実績
  4. PER × PBR（ミックス係数）で割安度判定
  5. ★ 売上高 → 右肩上がりか？
  6. ★ EPS（1株利益）→ 右肩上がりか？
  7. ★ 営業利益率 → 10%以上が理想
  8. ★ 自己資本比率 → 40%以上が安心
  9. ★ 営業CF → 毎年黒字か？
  10. ★ 1株配当 → 安定 or 増加トレンドか？

使い方: python3 run_screening.py
所要時間: 約10〜15分（候補数による）
"""

import json
import os
import re
import time
import urllib.request
import urllib.error
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SCREENING_FILE = os.path.join(BASE_DIR, "screening_data.json")
STOCKS_FILE = os.path.join(BASE_DIR, "stocks.json")

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")


# ============================================================
# ユーティリティ関数
# ============================================================

def clean_num(s):
    """'1,234' や '-567' → float。取得失敗→None"""
    if not s:
        return None
    s = s.replace(",", "").replace("円", "").replace("百万", "").strip()
    if s in ("-", "－", "―", "--", ""):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def analyze_trend(values):
    """数値リストのトレンドを判定（新しい順→古い順に並んでいる想定）

    Returns:
        dict: {
            "direction": "up" | "down" | "flat" | "volatile" | "unknown",
            "score": int (-3 ~ +3),
            "desc": str  (日本語の説明)
        }
    """
    # None除去して有効値だけ
    vals = [v for v in values if v is not None and v != 0]
    if len(vals) < 3:
        return {"direction": "unknown", "score": 0, "desc": "データ不足"}

    # 古い順に並び替え（リストが新しい順で来ることもある）
    # kabutan は上が新しい→下が古い なので reverse
    # ただし呼び出し元で既に古い→新しいの順にする

    n = len(vals)
    increases = 0
    decreases = 0
    for i in range(1, n):
        if vals[i] > vals[i - 1] * 1.01:  # 1%以上の増加
            increases += 1
        elif vals[i] < vals[i - 1] * 0.99:  # 1%以上の減少
            decreases += 1

    total_pairs = n - 1
    if total_pairs == 0:
        return {"direction": "unknown", "score": 0, "desc": "データ不足"}

    increase_ratio = increases / total_pairs
    decrease_ratio = decreases / total_pairs

    # 最初と最後の比較（全体で伸びたか）
    overall_growth = (vals[-1] - vals[0]) / abs(vals[0]) if vals[0] != 0 else 0

    if increase_ratio >= 0.7 and overall_growth > 0.1:
        return {"direction": "up", "score": 3, "desc": "右肩上がり↑"}
    elif increase_ratio >= 0.5 and overall_growth > 0.05:
        return {"direction": "up", "score": 2, "desc": "成長傾向↑"}
    elif decrease_ratio >= 0.7 and overall_growth < -0.1:
        return {"direction": "down", "score": -3, "desc": "右肩下がり↓"}
    elif decrease_ratio >= 0.5 and overall_growth < -0.05:
        return {"direction": "down", "score": -2, "desc": "縮小傾向↓"}
    elif abs(overall_growth) < 0.1 and increase_ratio < 0.5 and decrease_ratio < 0.5:
        return {"direction": "flat", "score": 0, "desc": "横ばい→"}
    else:
        return {"direction": "volatile", "score": -1, "desc": "不安定↕"}


# ============================================================
# データソース1: Yahoo!ファイナンス
# ============================================================

def fetch_from_yahoo_finance():
    """Yahoo!ファイナンス 高配当利回りランキングから取得（__PRELOADED_STATE__ JSON解析版）"""
    stocks = []
    for page in range(1, 4):
        try:
            url = f"https://finance.yahoo.co.jp/stocks/ranking/dividendYield?page={page}"
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=20) as resp:
                html = resp.read().decode("utf-8", errors="ignore")

            count = 0
            state_m = re.search(r'window\.__PRELOADED_STATE__\s*=\s*(\{.*?\});?\s*</script>', html, re.DOTALL)
            if state_m:
                try:
                    state = json.loads(state_m.group(1))
                    items = state.get("mainRankingList", {}).get("results", [])
                    for item in items:
                        code_str = item.get("stockCode", "")
                        if not code_str.isdigit() or int(code_str) < 1000:
                            continue
                        name = item.get("stockName", f"銘柄{code_str}")
                        # (株) などを除去
                        name = re.sub(r'^\(株\)', '', name).strip()
                        yld = 0
                        div_info = item.get("rankingResult", {}).get("shareDividendYield", {})
                        if div_info:
                            try:
                                yld = float(str(div_info.get("shareDividendYield", "0")).replace("+", ""))
                            except (ValueError, TypeError):
                                pass
                        if yld < 3.0:
                            continue
                        stocks.append({"code": int(code_str), "name": name, "yield": yld})
                        count += 1
                except (json.JSONDecodeError, KeyError, TypeError) as e:
                    log(f"  Yahoo JSON解析エラー: {e}")

            log(f"  Yahoo!ファイナンス ページ{page}: {count}銘柄")
            time.sleep(1)
        except Exception as e:
            log(f"  Yahoo!ファイナンス ページ{page}エラー: {e}")
    return stocks


# ============================================================
# データソース2: 投資の森
# ============================================================

def parse_toushi_table(html):
    """投資の森のテーブルHTMLをパース（複数列バリアント対応）

    パターン: code / name / 利回り% / [PBR列(optional)] / 増配/減配 / 株価
    hrefは /stock/dividend/XXXX/ または /stock/XXXX/ の両方に対応。
    """
    stocks = []
    seen = set()  # (code) で重複排除
    variants = [
        # V1: code name 利回り 増配 株価 （既存ページ / monthly / 50index / ranking_hd）
        (
            r'<a href="/stock/(?:dividend/)?(\d{4})/">\1</a>\s*</td>\s*'
            r'<td><a[^>]*>([^<]+)</a></td>\s*'
            r'<td>([\d.]+)%</td>\s*'
            r'<td[^>]*>((?:連続増配|非減配|減配)\d+|[－\-])</td>\s*'
            r'<td>([\d,]+)</td>'
        ),
        # V2: code name 利回り PBR 増配 株価 （stockrise_hd_pbr）
        (
            r'<a href="/stock/(?:dividend/)?(\d{4})/">\1</a>\s*</td>\s*'
            r'<td><a[^>]*>([^<]+)</a></td>\s*'
            r'<td>([\d.]+)%</td>\s*'
            r'<td>[\d.]+</td>\s*'
            r'<td[^>]*>((?:連続増配|非減配|減配)\d+|[－\-])</td>\s*'
            r'<td>([\d,]+)</td>'
        ),
    ]
    for pattern in variants:
        for code_str, name, yld_str, trend, price_str in re.findall(pattern, html, re.DOTALL):
            code = int(code_str)
            if code in seen:
                continue
            seen.add(code)
            stocks.append({
                "code": code,
                "name": name.strip(),
                "yield": float(yld_str),
                "div_trend": trend if trend not in ("－", "-") else "",
                "price": float(price_str.replace(",", ""))
            })
    return stocks


def fetch_from_toushi_no_mori():
    """投資の森 高配当ランキングから取得（全部盛り）"""
    stocks = []
    urls = [
        # 既存3ページ
        ("https://nikkeiyosoku.com/nisa/ranking_hold_hd_end/", "死ぬまで持ちたい高配当"),
        ("https://nikkeiyosoku.com/nisa/ranking_cheap_hd_crash/", "暴落時に欲しい高配当"),
        ("https://nikkeiyosoku.com/stock/ranking_hd/", "高配当総合"),
        # 追加4ページ（全部盛り）
        ("https://nikkeiyosoku.com/nisa/ranking_hd/", "NISA成長投資枠おすすめ高配当"),
        ("https://nikkeiyosoku.com/nisa/ranking_hold_hd_monthly/", "毎月配当金生活"),
        ("https://nikkeiyosoku.com/nisa/ranking_safe_hd_50index/", "日経高配当50指数"),
        ("https://nikkeiyosoku.com/nisa/ranking_stockrise_hd_pbr/", "PBR1倍以下×高配当"),
    ]
    seen_codes = set()
    for url, label in urls:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=20) as resp:
                html = resp.read().decode("utf-8", errors="ignore")

            parsed = parse_toushi_table(html)
            # ページ間でも重複排除（同じ銘柄が複数ランキングに出る想定）
            new_items = [s for s in parsed if s["code"] not in seen_codes]
            for s in new_items:
                seen_codes.add(s["code"])
            stocks.extend(new_items)
            log(f"  投資の森 {label}: {len(parsed)}件取得 / {len(new_items)}件新規")
            time.sleep(1)
        except Exception as e:
            log(f"  投資の森 {label}エラー: {e}")
    return stocks


def fetch_high_dividend_list():
    """Yahoo!ファイナンス + 投資の森 から高配当銘柄を取得"""
    all_stocks = []

    log("  [ソース1] Yahoo!ファイナンス...")
    yahoo_stocks = fetch_from_yahoo_finance()
    all_stocks.extend(yahoo_stocks)

    log("  [ソース2] 投資の森...")
    mori_stocks = fetch_from_toushi_no_mori()
    all_stocks.extend(mori_stocks)

    # 重複排除（先に取得した方を優先）
    seen = set()
    unique = []
    for s in all_stocks:
        if s["code"] not in seen:
            seen.add(s["code"])
            unique.append(s)

    log(f"  合計: {len(unique)}銘柄（重複排除後）")
    return unique


# ============================================================
# 財務データ取得（kabutan 決算ページ）
# ============================================================

def fetch_financial_history(code):
    """kabutan.jp の決算ページから過去の業績推移を取得

    取得項目:
    - 売上高（過去5〜10年）
    - 営業利益（過去5〜10年）
    - EPS / 1株益（過去5〜10年）
    - 1株配当（過去5〜10年）
    - 営業利益率（計算）
    - 自己資本比率

    kabutan テーブル構造:
    - 業績テーブル: 決算期, 売上高, 営業益, 経常益, 最終益, 修正1株益, 修正1株配, 発表日
    - 自己資本テーブル: 決算期, 1株純資産, 自己資本比率, 総資産, ...
    - CFテーブル: 決算期, 営業益, フリーCF, 営業CF, 投資CF, 財務CF, ...
    - 決算期フィールドに「I」「連」「単」「予」やUnicode空白が混在
    """
    result = {
        "revenue": [],       # 売上高 (古い→新しい)
        "op_profit": [],     # 営業利益
        "eps": [],           # EPS (1株益)
        "dividend_hist": [], # 1株配当
        "equity_ratio": None,  # 自己資本比率（直近）
        "op_margin": None,     # 営業利益率（直近）
    }

    def _clean_cell(c):
        """セルのHTMLタグと特殊文字を除去"""
        txt = re.sub(r'<[^>]+>', '', c)
        txt = txt.replace('&nbsp;', '').replace('\u3000', '').strip()
        return txt

    def _is_actual_period(period_text):
        """実績の決算期か判定（予想行・前期比行を除外）"""
        if "予" in period_text or "前期比" in period_text:
            return False
        return bool(re.search(r'\d{4}\.\d{2}', period_text))

    # --- 決算ページ ---
    try:
        url = f"https://kabutan.jp/stock/finance/?code={code}"
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=15) as resp:
            html = resp.read().decode("utf-8", errors="ignore")

        tables = re.findall(r'<table[^>]*>(.*?)</table>', html, re.DOTALL)

        # ==============================
        # 1. 業績テーブル（売上高 + 営業益 + 1株益 + 1株配）
        # ==============================
        for t in tables:
            if "売上" in t and ("1株益" in t or "修正1株益" in t):
                rows = re.findall(r'<tr[^>]*>(.*?)</tr>', t, re.DOTALL)
                for row in rows:
                    cells = re.findall(r'<t[dh][^>]*>(.*?)</t[dh]>', row, re.DOTALL)
                    if len(cells) < 7:
                        continue
                    ct = [_clean_cell(c) for c in cells]

                    # ヘッダー行・空行をスキップ
                    if not _is_actual_period(ct[0]):
                        continue

                    # 決算期, 売上高, 営業益, 経常益, 最終益, 修正1株益, 修正1株配, 発表日
                    rev = clean_num(ct[1])
                    op = clean_num(ct[2])
                    eps = clean_num(ct[5])
                    div = clean_num(ct[6])

                    result["revenue"].append(rev)
                    result["op_profit"].append(op)
                    result["eps"].append(eps)
                    result["dividend_hist"].append(div)
                break  # 最初の該当テーブルだけ

        # kabutan は古い順（上が古い）→ そのままでOK（古い→新しい）

        # ==============================
        # 2. 自己資本比率テーブル
        # ==============================
        for t in tables:
            t_text = re.sub(r'<[^>]+>', ' ', t)
            if "自己資本" in t_text and "比率" in t_text:
                rows = re.findall(r'<tr[^>]*>(.*?)</tr>', t, re.DOTALL)
                # 直近の実績行から自己資本比率を取得
                for row in rows:
                    cells = re.findall(r'<t[dh][^>]*>(.*?)</t[dh]>', row, re.DOTALL)
                    if len(cells) < 4:
                        continue
                    ct = [_clean_cell(c) for c in cells]
                    # ヘッダー行スキップ
                    if "決算期" in ct[0] or "自己資本" in ct[0]:
                        continue
                    if not re.search(r'\d{4}', ct[0]):
                        continue
                    # 自己資本比率は3列目（index 2）
                    eq = clean_num(ct[2])
                    if eq is not None and 0 < eq < 100:
                        result["equity_ratio"] = eq  # 最新値で上書き
                break

        # ==============================
        # 3. 営業利益率を計算（直近の実績データから）
        # ==============================
        if result["revenue"] and result["op_profit"]:
            # 最新の有効データを探す
            for i in range(len(result["revenue"]) - 1, -1, -1):
                rev_val = result["revenue"][i]
                op_val = result["op_profit"][i]
                if rev_val and rev_val > 0 and op_val is not None:
                    result["op_margin"] = round(op_val / rev_val * 100, 1)
                    break

    except Exception as e:
        log(f"    kabutan finance error {code}: {e}")

    # --- キャッシュフローページ ---
    result["op_cf"] = []  # 営業CF (古い→新しい)
    try:
        url_cf = f"https://kabutan.jp/stock/finance/?code={code}&type=C"
        req_cf = urllib.request.Request(url_cf, headers={"User-Agent": UA})
        with urllib.request.urlopen(req_cf, timeout=15) as resp_cf:
            html_cf = resp_cf.read().decode("utf-8", errors="ignore")

        tables_cf = re.findall(r'<table[^>]*>(.*?)</table>', html_cf, re.DOTALL)
        for t in tables_cf:
            if "営業CF" in t:
                rows = re.findall(r'<tr[^>]*>(.*?)</tr>', t, re.DOTALL)
                for row in rows:
                    cells = re.findall(r'<t[dh][^>]*>(.*?)</t[dh]>', row, re.DOTALL)
                    if len(cells) < 5:
                        continue
                    ct = [_clean_cell(c) for c in cells]
                    if not _is_actual_period(ct[0]):
                        continue
                    # CFテーブル: 決算期, 営業益, フリーCF, 営業CF, 投資CF, ...
                    # 営業CF は index 3
                    cf_val = clean_num(ct[3]) if len(ct) > 3 else None
                    if cf_val is not None:
                        result["op_cf"].append(cf_val)
                break

        # kabutan CF も古い順 → そのまま

    except Exception as e:
        log(f"    kabutan CF error {code}: {e}")

    return result


# ============================================================
# 銘柄詳細データ取得（統合）
# ============================================================

def fetch_stock_detail(code):
    """Yahoo Finance + minkabu + kabutan(決算のみ) から詳細データを取得"""
    info = {"code": code}

    # --- Yahoo Finance: 株価（最も安定、リトライ付き） ---
    for attempt in range(3):
        try:
            url_yf = f"https://finance.yahoo.co.jp/quote/{code}.T"
            req_yf = urllib.request.Request(url_yf, headers={"User-Agent": UA})
            with urllib.request.urlopen(req_yf, timeout=15) as resp_yf:
                html_yf = resp_yf.read().decode("utf-8", errors="ignore")

            # JSON "price" フィールド
            m_json = re.search(r'"price":\s*"?([\d,.]+)', html_yf)
            if m_json:
                p_yf = float(m_json.group(1).replace(",", ""))
                if 1 < p_yf < 500000:
                    info["price"] = p_yf

            # PBR from JSON
            m_pbr = re.search(r'"pbr":\s*"?([\d.]+)', html_yf)
            if m_pbr:
                try:
                    info["pbr"] = float(m_pbr.group(1))
                except ValueError:
                    pass

            # 株価フォールバック
            if "price" not in info:
                text_yf = re.sub(r'<[^>]+>', ' ', html_yf)
                for yv in re.findall(r'([\d,]+(?:\.\d+)?)\s*円', text_yf):
                    p = float(yv.replace(",", ""))
                    if 1 < p < 500000 and abs(p - 2838) > 1:
                        info["price"] = p
                        break

            break  # 成功したらリトライ終了
        except Exception as e:
            if attempt < 2:
                time.sleep(2)  # リトライ前に待機
            else:
                log(f"    Yahoo Finance error {code}: {e}")

    # --- minkabu メインページ: PER・PBR・株価（JSON-LDから） ---
    try:
        url_mk = f"https://minkabu.jp/stock/{code}"
        req_mk = urllib.request.Request(url_mk, headers={"User-Agent": UA})
        with urllib.request.urlopen(req_mk, timeout=15) as resp_mk:
            html_mk = resp_mk.read().decode("utf-8", errors="ignore")

        text_mk = re.sub(r'<[^>]+>', '|', html_mk)
        text_mk = re.sub(r'\s+', ' ', text_mk)

        # PER（調整後） — 実際のHTML: PER|(調整後)|||13.54倍
        m_per = re.search(r'PER\|[^倍]*?([\d,.]+)\s*倍', text_mk)
        if m_per:
            info["per"] = float(m_per.group(1).replace(",", ""))

        # PBR — 実際のHTML: PBR||1.84倍
        m_pbr = re.search(r'PBR\|[^倍]*?([\d,.]+)\s*倍', text_mk)
        if m_pbr:
            info["pbr"] = float(m_pbr.group(1).replace(",", ""))

        if "per" in info and "pbr" in info:
            info["mix_coef"] = round(info["per"] * info["pbr"], 2)

        # 株価フォールバック: minkabu JSON-LD（最初の"price"がOffer内の現在値）
        if "price" not in info:
            m_price = re.search(r'"offers":\s*\{[^}]*"price":\s*"?([\d,.]+)', html_mk)
            if m_price:
                p = float(m_price.group(1).replace(",", ""))
                if 1 < p < 500000:
                    info["price"] = p
                    log(f"    [{code}] minkabu JSON-LD 株価: {p}")

    except Exception as e:
        log(f"    minkabu main error {code}: {e}")

    # --- セクター: JPXデータ (all_stocks.json、キャッシュ) ---
    if not hasattr(fetch_stock_detail, '_sector_cache'):
        try:
            all_stocks_file = os.path.join(BASE_DIR, "all_stocks.json")
            with open(all_stocks_file, encoding="utf-8") as f:
                all_data = json.load(f)
            fetch_stock_detail._sector_cache = {
                str(s.get("code", "")): s.get("sector", "") for s in all_data
            }
        except Exception:
            fetch_stock_detail._sector_cache = {}
    sector = fetch_stock_detail._sector_cache.get(str(code), "")
    if sector:
        info["sector"] = sector

    time.sleep(0.1)

    # --- kabutan 決算ページ: 財務履歴（業績テーブルはkabutan固有） ---
    fin = fetch_financial_history(code)
    info["_financial"] = fin  # 内部用（スコアリングで使用）

    # 結果を info に格納
    if fin["equity_ratio"] is not None:
        info["equity_ratio"] = fin["equity_ratio"]
    if fin["op_margin"] is not None:
        info["op_margin"] = fin["op_margin"]

    # トレンド分析
    if len(fin["revenue"]) >= 3:
        info["_rev_trend"] = analyze_trend(fin["revenue"])
    if len(fin["eps"]) >= 3:
        info["_eps_trend"] = analyze_trend(fin["eps"])
    if len(fin["op_profit"]) >= 3:
        info["_op_trend"] = analyze_trend(fin["op_profit"])
    if len(fin["dividend_hist"]) >= 3:
        info["_div_hist_trend"] = analyze_trend(fin["dividend_hist"])

    # 営業CF: 全年黒字か？
    if fin["op_cf"]:
        positive_years = sum(1 for v in fin["op_cf"] if v > 0)
        total_years = len(fin["op_cf"])
        info["_cf_positive_ratio"] = positive_years / total_years if total_years > 0 else 0
        info["_cf_years"] = total_years

    time.sleep(0.1)

    # --- minkabu: 配当利回り, 配当性向, 増配実績 ---
    try:
        url2 = f"https://minkabu.jp/stock/{code}/dividend"
        req2 = urllib.request.Request(url2, headers={"User-Agent": UA})
        with urllib.request.urlopen(req2, timeout=15) as resp2:
            html2 = resp2.read().decode("utf-8", errors="ignore")
        text2 = re.sub(r'<[^>]+>', '|', html2)
        text2 = re.sub(r'\s+', ' ', text2)

        # 配当利回り
        m_yield = re.search(r'配当利回り\|[^|]*?\|?\s*([\d.]+)\s*%', text2)
        if m_yield:
            info["yield"] = float(m_yield.group(1))

        # 配当性向
        m = re.search(r'配当性向\|[^%]*?(\d+\.?\d+)%', text2)
        if m:
            info["payout_ratio"] = float(m.group(1))

        # 増配実績
        inc = re.search(r'(\d+)\s*(?:期|年)\s*連続\s*増配', html2)
        if inc:
            info["div_trend"] = f"{inc.group(1)}期連続増配"
        elif "非減配" in html2 or "減配なし" in html2:
            m_non = re.search(r'(\d+)\s*(?:期|年)\s*連続\s*非減配', html2)
            if m_non:
                info["div_trend"] = f"{m_non.group(1)}期連続非減配"
            else:
                info["div_trend"] = "非減配"

    except Exception as e:
        log(f"    minkabu error {code}: {e}")

    return info


# ============================================================
# リベ大流スコアリング（強化版）
# ============================================================

def score_stock(stock):
    """リベ大流スクリーニングスコア（強化版）

    チェック項目（IR BANKで確認する10項目を再現）:
    ① 配当利回り         (max +3)
    ② 配当性向           (max +3, 100%超→即失格)
    ③ 連続増配/非減配    (max +4)
    ④ ミックス係数       (max +3)
    ⑤ 売上高トレンド     (max +3)  ★NEW
    ⑥ EPSトレンド        (max +3)  ★NEW
    ⑦ 営業利益率         (max +2)  ★NEW
    ⑧ 自己資本比率       (max +2)  ★NEW
    ⑨ 営業CF             (max +2)  ★NEW
    ⑩ 1株配当トレンド    (max +2)  ★NEW
    ⑪ 高配当トラップ警戒 (max -2)

    理論上の最高スコア: 27
    推奨ライン: 15以上 → 優良、10以上 → 検討可
    """
    score = 0
    reasons = []
    details = {}  # 詳細データ（UIに表示用）

    yld = stock.get("yield", 0)
    pr = stock.get("payout_ratio", 999)
    mix = stock.get("mix_coef", 0)
    trend = stock.get("div_trend", "")

    # ① 配当利回り
    if yld >= 4.5:
        score += 3
        reasons.append(f"高利回り{yld:.1f}%")
    elif yld >= 4.0:
        score += 3
        reasons.append(f"高利回り{yld:.1f}%")
    elif yld >= 3.75:
        score += 2
        reasons.append(f"利回り{yld:.1f}%")
    elif yld >= 3.0:
        score += 1

    # ② 配当性向（100%超は即失格）
    if pr > 100:
        score -= 99
        reasons.append(f"⚠️配当性向{pr:.0f}%（失格）")
        stock["score"] = score
        stock["reason"] = "。".join(reasons[:3]) if reasons else ""
        stock["disqualified"] = True
        return score
    elif 0 < pr <= 40:
        score += 3
        reasons.append(f"配当性向{pr:.0f}%で増配余力大")
        details["payout"] = "◎"
    elif 0 < pr <= 50:
        score += 3
        reasons.append(f"配当性向{pr:.0f}%で余力十分")
        details["payout"] = "◎"
    elif 0 < pr <= 70:
        score += 2
        reasons.append(f"配当性向{pr:.0f}%で余力あり")
        details["payout"] = "○"
    elif pr > 70:
        score -= 1
        details["payout"] = "△"

    # ③ 連続増配/非減配
    if "連続増配" in trend:
        m = re.search(r'(\d+)', trend)
        years = int(m.group(1)) if m else 0
        if years >= 10:
            score += 4
            reasons.append(f"{years}期連続増配（高安定性）")
        elif years >= 5:
            score += 3
            reasons.append(f"{years}期連続増配")
        elif years >= 3:
            score += 2
            reasons.append(trend)
        else:
            score += 1
    elif "非減配" in trend:
        score += 1
        reasons.append("非減配実績あり")

    # ④ ミックス係数（割安度）
    if 0 < mix <= 11.25:
        score += 3
        reasons.append(f"超割安（ﾐｯｸｽ{mix:.1f}）")
        details["value"] = "◎"
    elif 0 < mix <= 22.5:
        score += 2
        reasons.append(f"割安（ﾐｯｸｽ{mix:.1f}）")
        details["value"] = "○"
    elif mix > 40:
        score -= 1
        details["value"] = "×"

    # ⑤ 売上高トレンド ★NEW
    rev_trend = stock.get("_rev_trend")
    if rev_trend:
        score += rev_trend["score"]
        if rev_trend["score"] >= 2:
            reasons.append(f"売上{rev_trend['desc']}")
        elif rev_trend["score"] <= -2:
            reasons.append(f"売上{rev_trend['desc']}")
        details["revenue"] = rev_trend["desc"]

    # ⑥ EPSトレンド ★NEW
    eps_trend = stock.get("_eps_trend")
    if eps_trend:
        score += eps_trend["score"]
        if eps_trend["score"] >= 2:
            reasons.append(f"EPS{eps_trend['desc']}")
        elif eps_trend["score"] <= -2:
            reasons.append(f"EPS{eps_trend['desc']}")
        details["eps"] = eps_trend["desc"]

    # ⑦ 営業利益率 ★NEW
    op_margin = stock.get("op_margin")
    if op_margin is not None:
        if op_margin >= 15:
            score += 2
            reasons.append(f"営業利益率{op_margin:.0f}%（高収益）")
            details["margin"] = "◎"
        elif op_margin >= 10:
            score += 2
            reasons.append(f"営業利益率{op_margin:.0f}%")
            details["margin"] = "○"
        elif op_margin >= 5:
            score += 1
            details["margin"] = "△"
        elif op_margin < 3:
            score -= 1
            details["margin"] = "×"

    # ⑧ 自己資本比率 ★NEW
    eq_ratio = stock.get("equity_ratio")
    if eq_ratio is not None:
        if eq_ratio >= 60:
            score += 2
            reasons.append(f"自己資本{eq_ratio:.0f}%（財務盤石）")
            details["equity"] = "◎"
        elif eq_ratio >= 40:
            score += 1
            details["equity"] = "○"
        elif eq_ratio < 20:
            score -= 2
            reasons.append(f"自己資本{eq_ratio:.0f}%（財務懸念）")
            details["equity"] = "×"
        elif eq_ratio < 30:
            score -= 1
            details["equity"] = "△"

    # ⑨ 営業CF ★NEW
    cf_ratio = stock.get("_cf_positive_ratio", 0)
    cf_years = stock.get("_cf_years", 0)
    if cf_years >= 3:
        if cf_ratio >= 1.0:
            score += 2
            reasons.append(f"営業CF{cf_years}年連続黒字")
            details["cf"] = "◎"
        elif cf_ratio >= 0.8:
            score += 1
            details["cf"] = "○"
        elif cf_ratio < 0.5:
            score -= 2
            reasons.append("営業CF赤字が多い")
            details["cf"] = "×"

    # ⑩ 1株配当トレンド ★NEW
    div_hist_trend = stock.get("_div_hist_trend")
    if div_hist_trend:
        if div_hist_trend["score"] >= 2:
            score += 2
            details["div_growth"] = "◎"
        elif div_hist_trend["score"] >= 1:
            score += 1
            details["div_growth"] = "○"
        elif div_hist_trend["score"] <= -2:
            score -= 1
            details["div_growth"] = "×"

    # ⑪ 高配当トラップ警戒
    if yld > 7:
        score -= 2
        reasons.append("利回り7%超（トラップ警戒）")
    elif yld > 6:
        score -= 1

    # 結果格納
    stock["score"] = score
    stock["reason"] = "。".join(reasons[:4]) if reasons else ""
    stock["_details"] = details

    # ランク判定
    if score >= 15:
        stock["rank"] = "S"
    elif score >= 12:
        stock["rank"] = "A"
    elif score >= 9:
        stock["rank"] = "B"
    elif score >= 6:
        stock["rank"] = "C"
    else:
        stock["rank"] = "D"

    return score


# ============================================================
# ディフェンシブセクター分類
# ============================================================

DEFENSIVE_SECTORS = {
    "食料品", "医薬品", "電気・ガス業", "情報・通信業",
    "陸運業", "倉庫・運輸関連業", "保険業", "銀行業",
    "不動産業", "サービス業"
}


# ============================================================
# メインスクリーニング処理
# ============================================================

def run_screening():
    """メインスクリーニング処理"""
    log("=" * 60)
    log("リベ大流 高配当株スクリーニング（強化版）開始")
    log("=" * 60)
    log("チェック項目: 利回り/配当性向/増配実績/割安度")
    log("             売上推移/EPS推移/営業利益率/自己資本比率")
    log("             営業CF/配当推移/トラップ警戒")
    log("=" * 60)

    # 1. 高配当ランキングから候補取得
    log("[1/4] 高配当ランキング取得中...")
    candidates = fetch_high_dividend_list()
    log(f"  → {len(candidates)}銘柄を取得")

    if not candidates:
        log("❌ 候補銘柄が取得できませんでした")
        return None

    # 2. 各銘柄の詳細データ取得（財務履歴を含む）— 5並列
    log(f"[2/4] {len(candidates)}銘柄の詳細データ取得中（5並列）...")
    log(f"  （1銘柄あたり株価+決算+CF+配当の4ページ取得）")
    from concurrent.futures import ThreadPoolExecutor, as_completed

    def _fetch_detail_worker(cand):
        code = cand["code"]
        detail = fetch_stock_detail(code)
        detail["name"] = cand["name"]
        if "yield" not in detail:
            detail["yield"] = cand.get("yield", 0)
        if "price" not in detail and cand.get("price", 0) > 0:
            detail["price"] = cand["price"]
        if cand.get("div_trend") and not detail.get("div_trend"):
            detail["div_trend"] = cand["div_trend"]
        return detail

    detailed = [None] * len(candidates)
    with ThreadPoolExecutor(max_workers=5) as executor:
        future_to_idx = {executor.submit(_fetch_detail_worker, cand): i for i, cand in enumerate(candidates)}
        done_count = 0
        for future in as_completed(future_to_idx):
            idx = future_to_idx[future]
            done_count += 1
            try:
                detail = future.result()
                detailed[idx] = detail
                code = detail["code"]
                name = detail["name"]
                parts = []
                if detail.get("_rev_trend"):
                    parts.append(f"売上{detail['_rev_trend']['desc']}")
                if detail.get("_eps_trend"):
                    parts.append(f"EPS{detail['_eps_trend']['desc']}")
                if detail.get("equity_ratio"):
                    parts.append(f"自己資本{detail['equity_ratio']:.0f}%")
                if detail.get("op_margin"):
                    parts.append(f"営業利益率{detail['op_margin']:.0f}%")
                summary = ', '.join(parts) if parts else ''
                log(f"  ({done_count}/{len(candidates)}) {code} {name}... {summary}")
            except Exception as e:
                cand = candidates[idx]
                log(f"  ({done_count}/{len(candidates)}) {cand['code']} {cand['name']}... エラー: {e}")
                detailed[idx] = {"code": cand["code"], "name": cand["name"], "yield": cand.get("yield", 0)}

    # None除去（念のため）
    detailed = [d for d in detailed if d is not None]

    # 3. スコアリングとフィルタリング
    log("[3/4] リベ大基準でスコアリング...")
    for stock in detailed:
        score_stock(stock)

    # スコア順にソート
    detailed.sort(key=lambda x: x.get("score", 0), reverse=True)

    # スコア分布ログ
    ranks = {"S": 0, "A": 0, "B": 0, "C": 0, "D": 0}
    disqualified = 0
    for s in detailed:
        if s.get("disqualified"):
            disqualified += 1
        else:
            r = s.get("rank", "D")
            ranks[r] = ranks.get(r, 0) + 1
    log(f"  スコア分布: S={ranks['S']} A={ranks['A']} B={ranks['B']} C={ranks['C']} D={ranks['D']} 失格={disqualified}")

    # --- エントリ作成のヘルパー ---
    def make_entry(s, include_financial=True):
        entry = {
            "code": s["code"],
            "name": s["name"],
            "yield": s.get("yield", 0),
            "price": s.get("price", 0),
            "sector": s.get("sector", ""),
            "score": s.get("score", 0),
            "rank": s.get("rank", "D"),
        }
        if s.get("per"):
            entry["per"] = s["per"]
        if s.get("pbr"):
            entry["pbr"] = s["pbr"]
        if s.get("mix_coef"):
            entry["mix_coef"] = s["mix_coef"]
        if s.get("payout_ratio"):
            entry["payout_ratio"] = s["payout_ratio"]
        if s.get("div_trend"):
            entry["div_trend"] = s["div_trend"]
        if s.get("reason"):
            entry["reason"] = s["reason"]

        # ★ 財務データ
        if include_financial:
            if s.get("equity_ratio") is not None:
                entry["equity_ratio"] = s["equity_ratio"]
            if s.get("op_margin") is not None:
                entry["op_margin"] = s["op_margin"]

            # トレンド情報
            for key, label in [
                ("_rev_trend", "revenue_trend"),
                ("_eps_trend", "eps_trend"),
                ("_op_trend", "op_profit_trend"),
                ("_div_hist_trend", "div_hist_trend"),
            ]:
                t = s.get(key)
                if t:
                    entry[label] = t["desc"]

            # 営業CF
            cf_ratio = s.get("_cf_positive_ratio", 0)
            cf_years = s.get("_cf_years", 0)
            if cf_years > 0:
                entry["cf_positive_years"] = f"{int(cf_ratio * cf_years)}/{cf_years}年黒字"

            # 詳細評価
            details = s.get("_details", {})
            if details:
                entry["evaluation"] = details

        return entry

    # --- 高配当ランキング（失格除外、利回り3.0%以上、スコア上位50） ---
    # リベ大の入口基準: 利回り3.75%以上だが、3.0%以上は許容（将来の増配期待）
    high_dividend = []
    for s in detailed:
        if s.get("disqualified"):
            continue
        if s.get("yield", 0) < 3.0:
            continue  # 高配当スクリーニングなので低利回りは除外
        if len(high_dividend) >= 50:
            break
        high_dividend.append(make_entry(s))

    # --- セクター別注目銘柄（各セクター上位3銘柄、利回り3.0%以上） ---
    sector_recs = {}
    for s in detailed:
        if s.get("disqualified"):
            continue
        if s.get("yield", 0) < 3.0:
            continue  # 高配当スクリーニングなので低利回りは除外
        sec = s.get("sector", "")
        if not sec:
            continue
        if sec not in sector_recs:
            sector_recs[sec] = []
        if len(sector_recs[sec]) < 3 and s.get("score", 0) >= 5:
            sector_recs[sec].append(make_entry(s))

    # --- 買い時ランキング（割安度 + 業績好調 重視） ---
    buy_timing = []
    for s in detailed:
        if s.get("disqualified"):
            continue
        mix = s.get("mix_coef", 0)
        sc = s.get("score", 0)
        yld_val = s.get("yield", 0)
        # 割安 + 利回り3.5%以上 + スコア8以上（財務面も良好な銘柄に限定）
        if mix > 0 and mix <= 22.5 and yld_val >= 3.5 and sc >= 8:
            buy_timing.append(make_entry(s))
    buy_timing.sort(key=lambda x: x.get("mix_coef", 99))
    buy_timing = buy_timing[:20]

    # 4. 結果保存
    log("[4/4] screening_data.json 保存中...")
    result = {
        "high_dividend_ranking": high_dividend,
        "buy_timing_ranking": buy_timing,
        "sector_recommendations": sector_recs,
        "screening_date": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "criteria": (
            "リベ大流（強化版）："
            "利回り3.75%↑/配当性向70%↓(100%超失格)/"
            "連続増配or非減配/ミックス係数/"
            "売上↑/EPS↑/営業利益率10%↑/"
            "自己資本比率40%↑/営業CF黒字/配当推移"
        ),
        "score_legend": {
            "S": "15点以上（超優良）",
            "A": "12〜14点（優良）",
            "B": "9〜11点（検討可）",
            "C": "6〜8点（要注意）",
            "D": "5点以下（非推奨）",
        },
    }

    # ガード：全カテゴリが空の場合は上書きせず中止（データ取得失敗の誤上書き防止）
    is_empty = (not high_dividend) and (not buy_timing) and (not sector_recs)
    if is_empty:
        log("⚠️ 全カテゴリが空のため保存を中止しました（既存 screening_data.json は保持）")
        log("   想定原因: 外部データソースの取得失敗、または条件に合致する銘柄が0件")
        log("   対処: 既存ファイルはそのまま。後ほど再実行してください。")
        return
    # 既存ファイルがあれば念のためバックアップ（.prev）
    try:
        if os.path.exists(SCREENING_FILE):
            import shutil
            shutil.copy2(SCREENING_FILE, SCREENING_FILE + ".prev")
    except Exception as e:
        log(f"  ⚠️ バックアップ失敗（続行）: {e}")

    with open(SCREENING_FILE, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    log(f"✅ 完了:")
    log(f"  高配当ランキング: {len(high_dividend)}銘柄")
    log(f"  買い時ランキング: {len(buy_timing)}銘柄")
    log(f"  セクター別推奨: {len(sector_recs)}セクター")
    log(f"  保存先: {SCREENING_FILE}")

    # トップ5表示
    if high_dividend:
        log("  --- トップ5 ---")
        for e in high_dividend[:5]:
            log(f"  {e['rank']} {e['code']} {e['name']} "
                f"利回り{e['yield']:.1f}% スコア{e['score']} "
                f"| {e.get('reason', '')}")

    return result


# ============================================================
# 配当成長ポテンシャル（IRバンク 10年データ）
# ============================================================

def fetch_irbank_dividend_history(code):
    """IRバンクから10年以上の分割調整済み配当履歴を取得

    Returns:
        list of (year, dividend): 古い順。分割調整済み。
        空リストの場合はデータ取得失敗。
    """
    try:
        url = f"https://irbank.net/{code}/dividend"
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=15) as resp:
            html = resp.read().decode("utf-8", errors="ignore")

        tables = re.findall(r"<table[^>]*>(.*?)</table>", html, re.DOTALL)
        if not tables:
            return []

        # ヘッダーで「分割調整」列の有無を確認
        rows = re.findall(r"<tr[^>]*>(.*?)</tr>", tables[0], re.DOTALL)
        if not rows:
            return []

        header_cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", rows[0], re.DOTALL)
        headers = [re.sub(r"<[^>]+>", "", c).strip() for c in header_cells]
        has_adjusted = "分割調整" in headers
        adj_idx = headers.index("分割調整") if has_adjusted else None
        total_idx = headers.index("合計") if "合計" in headers else None
        cat_idx = headers.index("区分") if "区分" in headers else None

        results = []
        for row in rows[1:]:
            cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.DOTALL)
            clean = [re.sub(r"<[^>]+>", "", c).strip() for c in cells]
            if len(clean) < 4:
                continue

            # 年度抽出
            m = re.search(r"(\d{4})年", clean[0])
            if not m:
                continue
            year = int(m.group(1))

            # 修正行スキップ
            if cat_idx is not None and len(clean) > cat_idx and clean[cat_idx] == "修正":
                continue

            # 未来の予想はスキップ
            if year > 2025:
                continue

            # 配当額を取得（分割調整列優先）
            div_val = None
            if has_adjusted and adj_idx is not None and len(clean) > adj_idx:
                raw = clean[adj_idx].replace(",", "").strip()
                if raw and raw != "-" and raw != "－":
                    try:
                        div_val = float(raw)
                    except ValueError:
                        pass

            if div_val is None and total_idx is not None and len(clean) > total_idx:
                raw = clean[total_idx].replace(",", "").strip()
                if raw and raw != "-" and raw != "－":
                    try:
                        div_val = float(raw)
                    except ValueError:
                        pass

            if div_val is not None and div_val > 0:
                results.append((year, div_val))

        # --- 分割検出 & 調整（分割調整列がない場合） ---
        if not has_adjusted and len(results) >= 2:
            # 前年比70%以上下落 → 株式分割と判定 → 分割後データのみ使用
            split_idx = None
            for i in range(1, len(results)):
                prev_div = results[i - 1][1]
                curr_div = results[i][1]
                if prev_div > 0 and curr_div / prev_div < 0.3:
                    split_idx = i  # この年以降を使う
                    log(f"    📌 {code}: {results[i-1][0]}→{results[i][0]}年に"
                        f"分割検出（{prev_div}→{curr_div}）。分割後データを使用")

            if split_idx is not None:
                results = results[split_idx:]

        return results

    except Exception as e:
        log(f"    IRバンク取得エラー ({code}): {e}")
        return []


def calc_dividend_growth_potential(code, name, current_price, current_yield):
    """配当成長ポテンシャルを計算

    Returns:
        dict or None
    """
    divs = fetch_irbank_dividend_history(code)
    if len(divs) < 5:
        return None

    years_span = divs[-1][0] - divs[0][0]
    if years_span < 4:
        return None

    first_div = divs[0][1]
    last_div = divs[-1][1]

    # 全期間CAGR
    total_cagr = (last_div / first_div) ** (1.0 / years_span) - 1

    # 直近5年CAGR（あれば）
    recent_5y = [(y, d) for y, d in divs if y >= divs[-1][0] - 5]
    if len(recent_5y) >= 3:
        r5_span = recent_5y[-1][0] - recent_5y[0][0]
        if r5_span > 0 and recent_5y[0][1] > 0:
            cagr_5y = (recent_5y[-1][1] / recent_5y[0][1]) ** (1.0 / r5_span) - 1
        else:
            cagr_5y = total_cagr
    else:
        cagr_5y = total_cagr

    # 保守的CAGR（全期間と直近5年の低い方を採用）
    conservative_cagr = min(total_cagr, cagr_5y)
    # さらに上限キャップ（年15%以上の成長が永続するとは考えにくい）
    conservative_cagr = min(conservative_cagr, 0.15)

    # 増配・減配回数チェック
    increases = 0
    decreases = 0
    consecutive_inc = 0
    max_consecutive_inc = 0
    for i in range(1, len(divs)):
        if divs[i][1] > divs[i - 1][1] * 1.005:  # 0.5%以上の増配
            increases += 1
            consecutive_inc += 1
            max_consecutive_inc = max(max_consecutive_inc, consecutive_inc)
        elif divs[i][1] < divs[i - 1][1] * 0.98:
            decreases += 1
            consecutive_inc = 0
        else:
            consecutive_inc = 0  # 横ばいは連続増配リセット
    # 直近から遡って現在の連続増配数を計算
    current_consecutive_inc = 0
    for i in range(len(divs) - 1, 0, -1):
        if divs[i][1] > divs[i - 1][1] * 1.005:
            current_consecutive_inc += 1
        else:
            break
    decrease_ratio = decreases / (len(divs) - 1) if len(divs) > 1 else 0

    # 5年後の予想配当
    future_div_5y = last_div * (1 + conservative_cagr) ** 5

    # 5年後の予想簿価利回り
    future_yield_5y = (future_div_5y / current_price * 100) if current_price > 0 else 0

    # 増配の安定性（年ごとの増配率のばらつき）
    growth_rates = []
    for i in range(1, len(divs)):
        if divs[i - 1][1] > 0:
            gr = (divs[i][1] / divs[i - 1][1]) - 1
            growth_rates.append(gr)

    stability = "安定" if growth_rates and max(growth_rates) - min(growth_rates) < 0.2 else "変動あり"

    return {
        "code": code,
        "name": name,
        "current_yield": round(current_yield, 2),
        "price": current_price,
        "cagr_total": round(total_cagr * 100, 1),
        "cagr_5y": round(cagr_5y * 100, 1),
        "cagr_used": round(conservative_cagr * 100, 1),
        "data_years": years_span,
        "future_div_5y": round(future_div_5y, 1),
        "future_yield_5y": round(future_yield_5y, 2),
        "increase_count": increases,
        "decrease_count": decreases,
        "consecutive_increase": current_consecutive_inc,
        "max_consecutive_increase": max_consecutive_inc,
        "decrease_ratio": round(decrease_ratio * 100, 0),
        "total_records": len(divs),
        "first_div": first_div,
        "last_div": last_div,
        "stability": stability,
    }


def run_growth_potential(detailed_stocks=None):
    """配当成長ポテンシャルランキングを生成

    Args:
        detailed_stocks: run_screening()で取得済みの詳細データ。
                         Noneの場合はscreening_data.jsonから読み込む。
    """
    log("")
    log("=" * 60)
    log("配当成長ポテンシャル分析（IRバンク 10年データ）")
    log("=" * 60)

    # stocks.json から株価のフォールバック用辞書を作成
    price_fallback = {}
    if os.path.exists(STOCKS_FILE):
        try:
            with open(STOCKS_FILE, encoding="utf-8") as f:
                stk_data = json.load(f)
            for s in stk_data:
                p = s.get("cur_price", 0) or s.get("buy_price", 0)
                if p > 0:
                    price_fallback[s["code"]] = p
        except Exception:
            pass

    # 対象銘柄を決定
    if detailed_stocks:
        candidates = [
            (s["code"], s["name"], s.get("price", 0) or price_fallback.get(s["code"], 0), s.get("yield", 0))
            for s in detailed_stocks
            if not s.get("disqualified")
        ]
    else:
        # screening_data.json から読み込み
        if os.path.exists(SCREENING_FILE):
            with open(SCREENING_FILE, encoding="utf-8") as f:
                data = json.load(f)
            candidates = []
            seen = set()
            for s in data.get("high_dividend_ranking", []):
                if s["code"] not in seen:
                    p = s.get("price", 0) or price_fallback.get(s["code"], 0)
                    candidates.append((s["code"], s["name"], p, s.get("yield", 0)))
                    seen.add(s["code"])
            # 利回り3%未満でもポテンシャルがある銘柄も含める
            for sec_stocks in data.get("sector_recommendations", {}).values():
                for s in sec_stocks:
                    if s["code"] not in seen:
                        p = s.get("price", 0) or price_fallback.get(s["code"], 0)
                        candidates.append((s["code"], s["name"], p, s.get("yield", 0)))
                        seen.add(s["code"])

    # price=0 の候補を除外
    candidates = [(c, n, p, y) for c, n, p, y in candidates if p > 0]

    log(f"  対象: {len(candidates)}銘柄")

    # IRバンクからデータ取得 & 計算（5並列）
    from concurrent.futures import ThreadPoolExecutor, as_completed

    def _growth_worker(args):
        code, name, price, yld = args
        return calc_dividend_growth_potential(code, name, price, yld)

    results = []
    with ThreadPoolExecutor(max_workers=5) as executor:
        future_to_info = {executor.submit(_growth_worker, c): c for c in candidates}
        done_count = 0
        for future in as_completed(future_to_info):
            done_count += 1
            code, name, price, yld = future_to_info[future]
            try:
                gp = future.result()
                if gp:
                    log(f"  ({done_count}/{len(candidates)}) {code} {name}... "
                        f"CAGR{gp['cagr_used']}%/年, 5年後利回り{gp['future_yield_5y']}%")
                    results.append(gp)
                else:
                    log(f"  ({done_count}/{len(candidates)}) {code} {name}... データ不足")
            except Exception as e:
                log(f"  ({done_count}/{len(candidates)}) {code} {name}... エラー: {e}")

    # 5年後の予想利回りでソート
    results.sort(key=lambda x: x["future_yield_5y"], reverse=True)

    # 上位30銘柄
    growth_ranking = results[:30]

    log(f"")
    log(f"✅ 配当成長ポテンシャル分析完了:")
    log(f"  分析対象: {len(results)}銘柄")
    log(f"  ランキング: 上位{len(growth_ranking)}銘柄")

    if growth_ranking:
        log("  --- トップ5 ---")
        for g in growth_ranking[:5]:
            log(f"  {g['code']} {g['name']} "
                f"現在{g['current_yield']}% → 5年後{g['future_yield_5y']}% "
                f"(CAGR{g['cagr_used']}%/年, {g['data_years']}年実績)")

    return growth_ranking


def run_screening_with_growth():
    """通常スクリーニング + 配当成長ポテンシャルを実行"""
    result = run_screening()
    if result is None:
        return

    # 配当成長ポテンシャル
    log("")
    log("[追加] 配当成長ポテンシャル分析...")

    # screening_data.json から全銘柄を取得
    growth_ranking = run_growth_potential()

    # 結果をscreening_data.jsonに追記
    with open(SCREENING_FILE, encoding="utf-8") as f:
        data = json.load(f)

    data["growth_potential_ranking"] = growth_ranking
    data["growth_note"] = (
        "過去実績ベースの参考値。「このペースが続いた場合」の条件付き予想。"
        "CAGR は全期間と直近5年の低い方（保守的）を採用。上限15%でキャップ。"
    )

    with open(SCREENING_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    log(f"💾 配当成長ポテンシャルを screening_data.json に追加保存")


if __name__ == "__main__":
    run_screening_with_growth()
