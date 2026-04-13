"""高配当株ポートフォリオ ダッシュボード - Webサーバー"""

import http.server
import json
import os
import csv
import io
import re
import subprocess
import threading
import time
import urllib.request
import urllib.error

PORT = int(os.environ.get("PORT", 8080))
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STOCKS_FILE = os.path.join(BASE_DIR, "stocks.json")
SCREENING_FILE = os.path.join(BASE_DIR, "screening_data.json")
ALL_STOCKS_FILE = os.path.join(BASE_DIR, "all_stocks.json")
INDEX_FILE = os.path.join(BASE_DIR, "index.html")


def load_stocks():
    if os.path.exists(STOCKS_FILE):
        with open(STOCKS_FILE, encoding="utf-8") as f:
            return json.load(f)
    return []


def save_stocks(data):
    with open(STOCKS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _clean_num(s):
    """'1,908' や '51,625円' → float"""
    s = s.replace(",", "").replace("円", "").replace("%", "").strip()
    if not s or s == "-":
        return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


def parse_mf_text(text):
    """Parse MoneyForward ME data (CSV, TSV, or pasted table) into stocks list"""
    stocks = []
    # Auto-detect delimiter: tab or comma
    if "\t" in text:
        reader = csv.reader(io.StringIO(text), delimiter="\t")
    else:
        reader = csv.reader(io.StringIO(text))

    for row in reader:
        if len(row) < 5:
            continue
        code_str = row[0].strip().replace('"', '')
        try:
            code = int(code_str)
        except (ValueError, IndexError):
            continue
        name = row[1].strip()
        shares = int(_clean_num(row[2]))
        buy_price = _clean_num(row[3])
        cur_price = _clean_num(row[4])
        if shares == 0 and buy_price == 0:
            continue
        stocks.append({
            "code": code, "name": name, "shares": shares,
            "buy_price": buy_price, "cur_price": cur_price,
            "annual_div": 0, "mid_div": 0, "mid_month": 12, "end_month": 6,
            "nisa": "課税", "sector": ""
        })
    return stocks


UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"


def _load_all_stocks_sectors():
    """all_stocks.jsonからセクター情報を辞書(code→sector)で返す"""
    if os.path.exists(ALL_STOCKS_FILE):
        with open(ALL_STOCKS_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return {str(s.get("code", "")): s.get("sector", "") for s in data}
    return {}

_sector_cache = None

def _get_sector(code):
    """JPXデータからセクターを取得（キャッシュ付き）"""
    global _sector_cache
    if _sector_cache is None:
        _sector_cache = _load_all_stocks_sectors()
    return _sector_cache.get(str(code), "")


def fetch_stock_info(code):
    """Yahoo Finance + minkabu から株価・PER・PBR・配当情報を取得
    kabutan不使用 — HTML構造変更に強い安定ソース構成:
      1. Yahoo Finance → 株価 (JSON "price" フィールド)、PBR
      2. minkabu メインページ → PER、PBR
      3. minkabu 配当ページ → 配当利回り、配当性向、増配実績
      4. 配当額 = 株価 × 利回り で算出
      5. all_stocks.json → セクター (JPXデータ)
    """
    info = {}
    cur_price = 0
    yf_pbr = 0

    # === 1. Yahoo Finance: 株価（最も安定） ===
    try:
        url_yf = f"https://finance.yahoo.co.jp/quote/{code}.T"
        req_yf = urllib.request.Request(url_yf, headers={"User-Agent": UA})
        with urllib.request.urlopen(req_yf, timeout=15) as resp_yf:
            html_yf = resp_yf.read().decode("utf-8", errors="ignore")

        # JSON内の "price" フィールド（最も正確）
        m_json = re.search(r'"price":\s*"?([\d,.]+)', html_yf)
        if m_json:
            p_yf = float(m_json.group(1).replace(",", ""))
            if 1 < p_yf < 500000:
                cur_price = p_yf

        # Yahoo FinanceのPBR (JSON)
        m_pbr_yf = re.search(r'"pbr":\s*"?([\d.]+)', html_yf)
        if m_pbr_yf:
            try:
                yf_pbr = float(m_pbr_yf.group(1))
            except ValueError:
                pass

        # 株価フォールバック: テキストから（2,838円広告を除外）
        if cur_price == 0:
            text_yf = re.sub(r'<[^>]+>', ' ', html_yf)
            yen_vals = re.findall(r'([\d,]+(?:\.\d+)?)\s*円', text_yf)
            for yv in yen_vals:
                p_yf = float(yv.replace(",", ""))
                if 1 < p_yf < 500000 and abs(p_yf - 2838) > 1:
                    cur_price = p_yf
                    break

        if cur_price > 0:
            print(f"  [{code}] Yahoo Finance 株価: {cur_price}")
        else:
            print(f"  [{code}] ⚠️ Yahoo Finance 株価取得失敗")

    except Exception as e:
        print(f"  [{code}] Yahoo Finance error: {e}")

    # === 2. minkabu メインページ: PER・PBR ===
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
        elif yf_pbr > 0:
            info["pbr"] = yf_pbr

        # ミックス係数
        if "per" in info and "pbr" in info:
            info["mix_coef"] = round(info["per"] * info["pbr"], 2)

        # 株価フォールバック: minkabuから（Yahoo Financeが失敗した場合）
        if cur_price == 0:
            for m in re.finditer(r'([\d,]+(?:\.\d+)?)\|?\s*円', text_mk):
                val = float(m.group(1).replace(",", ""))
                if 100 < val < 500000:
                    # 目標株価を除外（前後のコンテキストを確認）
                    ctx = text_mk[max(0, m.start()-30):m.start()]
                    if "目標" not in ctx:
                        cur_price = val
                        print(f"  [{code}] minkabu 株価フォールバック: {cur_price}")
                        break

    except Exception as e:
        print(f"  [{code}] minkabu main error: {e}")

    # === 3. minkabu 配当ページ: 利回り・配当性向・増配実績 ===
    minkabu_yield = 0
    try:
        url_div = f"https://minkabu.jp/stock/{code}/dividend"
        req_div = urllib.request.Request(url_div, headers={"User-Agent": UA})
        with urllib.request.urlopen(req_div, timeout=15) as resp_div:
            html_div = resp_div.read().decode("utf-8", errors="ignore")

        text_div = re.sub(r'<[^>]+>', '|', html_div)
        text_div = re.sub(r'\s+', ' ', text_div)

        # 配当利回り
        m_yield = re.search(r'配当利回り\|[^|]*?\|?\s*([\d.]+)\s*%', text_div)
        if m_yield:
            minkabu_yield = float(m_yield.group(1))

        # 配当性向
        m_payout = re.search(r'配当性向\|[^%]*?(\d+\.?\d+)%', text_div)
        if m_payout:
            info["payout_ratio"] = float(m_payout.group(1))

        # 増配実績
        inc = re.search(r'(\d+)\s*(?:期|年)\s*連続\s*増配', html_div)
        if inc:
            info["div_trend"] = f"{inc.group(1)}期連続増配"
        elif "非減配" in html_div:
            m_non = re.search(r'(\d+)\s*(?:期|年)\s*連続\s*非減配', html_div)
            if m_non:
                info["div_trend"] = f"{m_non.group(1)}期連続非減配"

    except Exception as e:
        print(f"  [{code}] minkabu dividend error: {e}")

    # === 4. 配当額の算出: 株価 × 利回り（最も信頼性が高い方法） ===
    if cur_price > 0 and minkabu_yield > 0:
        annual_div = round(cur_price * minkabu_yield / 100, 1)
        info["annual_div"] = annual_div
        info["mid_div"] = round(annual_div / 2, 1)
        print(f"  [{code}] 配当: {annual_div}円 (株価{cur_price} × 利回り{minkabu_yield}%)")

    # === 5. 株価をinfoに格納 ===
    if cur_price > 0:
        info["price"] = cur_price

    # === 6. セクター: JPXデータ (all_stocks.json) ===
    sector = _get_sector(code)
    if sector:
        info["sector"] = sector

    # === 利回り異常チェック ===
    if cur_price > 0 and info.get("annual_div", 0) > 0:
        calc_yield = info["annual_div"] / cur_price * 100
        if calc_yield > 10:
            print(f"  ⚠️ [{code}] 利回り{calc_yield:.1f}%が異常に高い。要確認。")

    return info


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/api/stocks":
            data = load_stocks()
            self._json_response(data)
        elif self.path == "/api/screening":
            if os.path.exists(SCREENING_FILE):
                with open(SCREENING_FILE, encoding="utf-8") as f:
                    self._json_response(json.load(f))
            else:
                self._json_response({"high_dividend_ranking": [], "buy_timing_ranking": []})
        elif self.path == "/api/all_stocks":
            if os.path.exists(ALL_STOCKS_FILE):
                with open(ALL_STOCKS_FILE, encoding="utf-8") as f:
                    self._json_response(json.load(f))
            else:
                self._json_response([])
        elif self.path.startswith("/api/stock_info?"):
            # /api/stock_info?code=7181 → kabutan/minkabuから銘柄情報を取得
            import urllib.parse as up
            params = up.parse_qs(up.urlparse(self.path).query)
            code_str = params.get("code", [""])[0]
            if not code_str.isdigit():
                self._json_response({"error": "invalid code"}, 400)
                return
            code = int(code_str)
            print(f"[API] stock_info: {code} を取得中...")
            info = fetch_stock_info(code)
            info["code"] = code
            # 利回り計算
            price = info.get("price", 0)
            annual_div = info.get("annual_div", 0)
            if price > 0 and annual_div > 0:
                info["yield"] = round(annual_div / price * 100, 2)
            print(f"[API] stock_info: {code} → price={info.get('price',0)}, yield={info.get('yield',0)}%, sector={info.get('sector','')}")
            self._json_response(info)
        elif self.path == "/" or self.path == "/index.html":
            self._serve_file(INDEX_FILE, "text/html")
        elif self.path == "/manifest.json":
            self._serve_file(os.path.join(BASE_DIR, "manifest.json"), "application/manifest+json")
        elif self.path == "/sw.js":
            fpath = os.path.join(BASE_DIR, "sw.js")
            if os.path.exists(fpath):
                with open(fpath, "rb") as f:
                    data = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "application/javascript; charset=utf-8")
                self.send_header("Content-Length", len(data))
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Service-Worker-Allowed", "/")
                self.end_headers()
                self.wfile.write(data)
            else:
                self.send_error(404)
        elif self.path.startswith("/icon-") and self.path.endswith(".svg"):
            fname = self.path.lstrip("/")
            self._serve_file(os.path.join(BASE_DIR, fname), "image/svg+xml")
        else:
            self.send_error(404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)

        if self.path == "/api/stocks":
            try:
                data = json.loads(body.decode("utf-8"))
                save_stocks(data)
                self._json_response({"ok": True, "count": len(data)})
            except Exception as e:
                self._json_response({"error": str(e)}, 400)

        elif self.path == "/api/simulate":
            try:
                req = json.loads(body.decode("utf-8"))
                stocks = load_stocks()
                code = req.get("code")
                add_shares = req.get("add_shares", 0)
                add_price = req.get("add_price", 0)
                stock = next((s for s in stocks if s["code"] == code), None)
                if not stock:
                    self._json_response({"error": "stock not found"}, 404)
                    return
                old_total = stock["shares"] * stock["buy_price"]
                new_total = old_total + add_shares * add_price
                new_shares = stock["shares"] + add_shares
                new_avg = new_total / new_shares if new_shares else 0
                old_yield = (stock["annual_div"] / stock["buy_price"] * 100) if stock["buy_price"] else 0
                new_yield = (stock["annual_div"] / new_avg * 100) if new_avg else 0
                self._json_response({
                    "old_shares": stock["shares"], "new_shares": new_shares,
                    "old_avg": stock["buy_price"], "new_avg": round(new_avg, 1),
                    "old_yield": round(old_yield, 2), "new_yield": round(new_yield, 2),
                    "add_cost": add_shares * add_price,
                    "add_annual_div": stock["annual_div"] * add_shares
                })
            except Exception as e:
                self._json_response({"error": str(e)}, 400)

        elif self.path == "/api/update-prices":
            try:
                # Run update_prices.py synchronously
                script = os.path.join(BASE_DIR, "update_prices.py")
                result = subprocess.run(
                    ["python3", script],
                    capture_output=True, text=True, timeout=600
                )
                # Parse output for counts
                output = result.stdout + result.stderr
                import re
                ok_match = re.findall(r'(\d+)件更新', output)
                ng_match = re.findall(r'(\d+)件失敗', output)
                total_ok = sum(int(m) for m in ok_match) if ok_match else 0
                total_ng = sum(int(m) for m in ng_match) if ng_match else 0
                self._json_response({"ok": True, "updated": total_ok, "failed": total_ng, "output": output[-500:]})
            except subprocess.TimeoutExpired:
                self._json_response({"error": "タイムアウト（10分超過）"}, 500)
            except Exception as e:
                self._json_response({"error": str(e)}, 500)

        elif self.path == "/api/import-csv":
            try:
                csv_text = body.decode("utf-8")
                imported = parse_mf_text(csv_text)
                existing = load_stocks()
                merged = list(existing)
                for s in imported:
                    found = False
                    for i, e in enumerate(merged):
                        if e["code"] == s["code"]:
                            merged[i]["shares"] = s["shares"]
                            merged[i]["buy_price"] = s["buy_price"]
                            merged[i]["cur_price"] = s["cur_price"]
                            merged[i]["name"] = s["name"]
                            found = True
                            break
                    if not found:
                        merged.append(s)
                save_stocks(merged)
                self._json_response({"ok": True, "imported": len(imported), "total": len(merged)})
            except Exception as e:
                self._json_response({"error": str(e)}, 400)

        elif self.path == "/api/import-paste":
            try:
                text = body.decode("utf-8")
                imported = parse_mf_text(text)
                if not imported:
                    self._json_response({"error": "有効なデータが見つかりません"}, 400)
                    return
                existing = load_stocks()
                existing_map = {s["code"]: s for s in existing}
                merged = []
                seen = set()
                # Update existing with imported data
                for s in imported:
                    code = s["code"]
                    seen.add(code)
                    if code in existing_map:
                        entry = existing_map[code].copy()
                        entry["shares"] = s["shares"]
                        entry["buy_price"] = s["buy_price"]
                        entry["cur_price"] = s["cur_price"]
                        entry["name"] = s["name"]
                    else:
                        entry = s
                    merged.append(entry)
                # Keep stocks not in import (user might have manually added)
                for s in existing:
                    if s["code"] not in seen:
                        merged.append(s)
                save_stocks(merged)
                self._json_response({"ok": True, "imported": len(imported), "total": len(merged)})
            except Exception as e:
                self._json_response({"error": str(e)}, 400)

        elif self.path == "/api/enrich-stocks":
            self._handle_enrich(body)

        elif self.path == "/api/run-screening":
            self._handle_screening()

        else:
            self.send_error(404)

    def _handle_enrich(self, body):
        """銘柄データ更新 - ストリーミングでプログレス返す"""
        try:
            req = json.loads(body.decode("utf-8"))
            only_missing = req.get("only_missing", False)
        except Exception:
            only_missing = False

        stocks = load_stocks()
        if only_missing:
            targets = [s for s in stocks if not s.get("annual_div") or not s.get("sector")]
        else:
            targets = list(stocks)

        # Start streaming response
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()

        updated = 0
        stock_map = {s["code"]: s for s in stocks}

        for i, target in enumerate(targets):
            code = target["code"]
            name = target["name"]
            pct = int((i + 1) / len(targets) * 100)
            try:
                info = fetch_stock_info(code)
                if info:
                    stock_map[code].update(info)
                    updated += 1
                    parts = []
                    if "annual_div" in info:
                        parts.append(f"配当{info['annual_div']}円")
                    if "sector" in info:
                        parts.append(info["sector"])
                    if "per" in info:
                        parts.append(f"PER{info['per']}")
                    result = " / ".join(parts) if parts else "一部取得"
                else:
                    result = "取得失敗"
            except Exception as e:
                result = f"エラー: {e}"

            line = json.dumps({
                "type": "progress", "current": i + 1, "total": len(targets),
                "pct": pct, "code": code, "name": name, "result": result
            }, ensure_ascii=False) + "\n"
            self._send_chunk(line)
            time.sleep(1.5)  # Rate limit

        # Save updated stocks
        save_stocks(list(stock_map.values()))

        done_line = json.dumps({
            "type": "done", "updated": updated, "total": len(targets)
        }, ensure_ascii=False) + "\n"
        self._send_chunk(done_line)
        self._send_chunk("")  # End chunked

    def _handle_screening(self):
        """リベ大流スクリーニングを実行（バックグラウンドで実行、ストリーミングで進捗返す）"""
        try:
            script = os.path.join(BASE_DIR, "run_screening.py")
            if not os.path.exists(script):
                self._json_response({"error": "run_screening.py が見つかりません"}, 404)
                return

            # ストリーミングで進捗を返す
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()

            # subprocessでリアルタイム出力
            proc = subprocess.Popen(
                ["python3", script],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1
            )

            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue
                msg = json.dumps({
                    "type": "progress", "message": line
                }, ensure_ascii=False) + "\n"
                self._send_chunk(msg)

            proc.wait()

            if proc.returncode == 0:
                # スクリーニング結果を読み込んで返す
                done_line = json.dumps({
                    "type": "done", "message": "スクリーニング完了"
                }, ensure_ascii=False) + "\n"
            else:
                done_line = json.dumps({
                    "type": "error", "message": f"スクリーニング失敗 (exit code {proc.returncode})"
                }, ensure_ascii=False) + "\n"

            self._send_chunk(done_line)
            self._send_chunk("")

        except Exception as e:
            try:
                err_line = json.dumps({
                    "type": "error", "message": str(e)
                }, ensure_ascii=False) + "\n"
                self._send_chunk(err_line)
                self._send_chunk("")
            except Exception:
                pass

    def _send_chunk(self, data):
        """Send a chunk in chunked transfer encoding"""
        chunk = data.encode("utf-8")
        self.wfile.write(f"{len(chunk):x}\r\n".encode())
        self.wfile.write(chunk)
        self.wfile.write(b"\r\n")
        self.wfile.flush()

    def _json_response(self, data, code=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", len(body))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _serve_file(self, path, content_type):
        if os.path.exists(path):
            with open(path, "rb") as f:
                data = f.read()
            self.send_response(200)
            self.send_header("Content-Type", f"{content_type}; charset=utf-8")
            self.send_header("Content-Length", len(data))
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.end_headers()
            self.wfile.write(data)
        else:
            self.send_error(404)

    def log_message(self, fmt, *args):
        print(f"[dashboard] {args[0]}")


if __name__ == "__main__":
    with http.server.HTTPServer(("", PORT), Handler) as srv:
        print(f"Dashboard running at http://localhost:{PORT}")
        srv.serve_forever()
