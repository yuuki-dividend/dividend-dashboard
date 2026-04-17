// Shared scraping helpers for Vercel Serverless Functions.
// Mirrors the logic of the local Python scrapers (update_all.py / server.py),
// but runs server-side on Vercel so we never depend on CORS proxies.
//
// Sources (same as local):
//   - Yahoo Finance    : https://finance.yahoo.co.jp/quote/{code}.T
//   - IR BANK          : https://irbank.net/{code}
//   - みんかぶ (minkabu): https://minkabu.jp/stock/{code}/dividend   (配当ページ)
//
// Primary dividend formula (same as server.py):
//   annual_div = price × minkabu_yield / 100       (round to 1 decimal)

const UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36';

// ---------- HTTP ----------
// NOTE: Vercel Serverless には 60s の maxDuration 上限がある。
// 1 銘柄を 30s で諦めれば、concurrency=8 で batch=20 の場合、
// 最悪 (20/8) * 30s = 75s だが、各 chunk 内は 10s + 10s + 10s = 30s が上限なので OK。
async function fetchText(url, timeoutMs = 10000, retries = 0) {
  for (let attempt = 0; attempt <= retries; attempt++) {
    const controller = new AbortController();
    const t = setTimeout(() => controller.abort(), timeoutMs);
    try {
      const resp = await fetch(url, {
        method: 'GET',
        headers: {
          'User-Agent': UA,
          'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
          'Accept-Language': 'ja,en;q=0.9',
          'Accept-Encoding': 'gzip, deflate, br',
        },
        signal: controller.signal,
        redirect: 'follow',
      });
      clearTimeout(t);
      if (!resp.ok) {
        if (attempt < retries) continue;
        throw new Error(`HTTP ${resp.status} ${resp.statusText}`);
      }
      const html = await resp.text();
      if (!html || html.length < 100) {
        if (attempt < retries) continue;
        throw new Error('Empty response body');
      }
      return html;
    } catch (e) {
      clearTimeout(t);
      if (attempt < retries) {
        await new Promise(r => setTimeout(r, 500));
        continue;
      }
      throw e;
    }
  }
}

function stripHtml(html) {
  // Replace tags with a single space (matches server.py's text-mode regex)
  return html.replace(/<[^>]+>/g, ' ').replace(/&[a-z]+;/g, ' ').replace(/\s+/g, ' ');
}

function stripToPipe(html) {
  // minkabu patterns rely on pipe-separated cells (matches update_all.py)
  return html.replace(/<[^>]+>/g, '|').replace(/\s+/g, ' ');
}

function toNum(s) {
  if (s == null) return null;
  const n = parseFloat(String(s).replace(/[, ]/g, ''));
  return Number.isFinite(n) ? n : null;
}

function inRange(v, min, max) {
  return v != null && v >= min && v <= max;
}

// ---------- Yahoo Finance ----------
async function scrapeYahoo(code) {
  const out = { price: null, per_forecast: null, pbr_actual: null, pbr_json: null, yield: null };
  let html;
  try {
    html = await fetchText(`https://finance.yahoo.co.jp/quote/${code}.T`);
  } catch (e) {
    out._error = `yahoo: ${e.message}`;
    return out;
  }
  // JSON price
  let m = html.match(/"price":\s*"?([\d,.]+)/);
  if (m) {
    const v = toNum(m[1]);
    if (inRange(v, 1, 500000)) out.price = v;
  }
  // JSON pbr (fallback)
  m = html.match(/"pbr":\s*"?([\d.]+)/);
  if (m) {
    const v = toNum(m[1]);
    if (inRange(v, 0.01, 100)) out.pbr_json = v;
  }
  const text = stripHtml(html);
  // PER(会社予想) → PER(連結予想) → PER(無印)
  const perPats = [
    /PER\s*[（(]会社予想[）)][^倍]{0,40}?(\d+(?:\.\d+)?)\s*倍/,
    /PER\s*[（(]連結予想[）)][^倍]{0,40}?(\d+(?:\.\d+)?)\s*倍/,
    /PER[^倍]{0,30}?(\d+(?:\.\d+)?)\s*倍/,
  ];
  for (const p of perPats) {
    const mm = text.match(p);
    if (mm) { const v = toNum(mm[1]); if (inRange(v, 0.1, 500)) { out.per_forecast = v; break; } }
  }
  // PBR(実績) → PBR(連結) → PBR(無印)
  const pbrPats = [
    /PBR\s*[（(]実績[）)][^倍]{0,40}?(\d+(?:\.\d+)?)\s*倍/,
    /PBR\s*[（(]連結[）)][^倍]{0,40}?(\d+(?:\.\d+)?)\s*倍/,
    /PBR[^倍]{0,30}?(\d+(?:\.\d+)?)\s*倍/,
  ];
  for (const p of pbrPats) {
    const mm = text.match(p);
    if (mm) { const v = toNum(mm[1]); if (inRange(v, 0.01, 100)) { out.pbr_actual = v; break; } }
  }
  // 配当利回り (fallback)
  const yPats = [
    /配当利回り\s*[（(]会社予想[）)][^%]{0,40}?(\d+(?:\.\d+)?)\s*%/,
    /配当利回り[^%]{0,30}?(\d+(?:\.\d+)?)\s*%/,
  ];
  for (const p of yPats) {
    const mm = text.match(p);
    if (mm) { const v = toNum(mm[1]); if (inRange(v, 0, 20)) { out.yield = v; break; } }
  }
  return out;
}

// ---------- IR BANK ----------
async function scrapeIrBank(code) {
  const out = { per_forecast: null, per_actual: null, pbr: null, equity_ratio: null, fiscal_month: null };
  let html;
  try {
    html = await fetchText(`https://irbank.net/${code}`);
  } catch (e) {
    out._error = `irbank: ${e.message}`;
    return out;
  }
  const text = stripHtml(html);
  let m = text.match(/PER[^倍]{0,25}?予\s*(\d+(?:\.\d+)?)\s*倍/);
  if (m) { const v = toNum(m[1]); if (inRange(v, 0.1, 500)) out.per_forecast = v; }
  m = text.match(/PER\s*[（(]連[）)]\s*(\d+(?:\.\d+)?)\s*倍/);
  if (m) { const v = toNum(m[1]); if (inRange(v, 0.1, 500)) out.per_actual = v; }
  m = text.match(/PBR\s*[（(]連[）)]\s*(\d+(?:\.\d+)?)\s*倍/);
  if (m) { const v = toNum(m[1]); if (inRange(v, 0.01, 100)) out.pbr = v; }
  m = text.match(/自己資本比率[^%]{0,25}?(\d+(?:\.\d+)?)\s*%/);
  if (m) { const v = toNum(m[1]); if (inRange(v, 0, 100)) out.equity_ratio = v; }
  // 決算月: IR BANK は「2026年3月期」「2025年12月期」のように書く
  //   マックス(6454) の例: "2026年3月期決算短信〔日本基準〕(連結)"
  //   オカムラ(7994) の例: "2026年3月期..."
  //   他: "決算月 3月" / "決算期 2025/03" 等
  const fmPats = [
    /\d{4}\s*年\s*(\d{1,2})\s*月期/,               // "2026年3月期" ← IR BANK メイン
    /決算月[^0-9]{0,10}?(\d{1,2})\s*月/,
    /決算期[^0-9]{0,10}?\d{4}\s*[\/年\-]\s*(\d{1,2})/,
    /本決算[^0-9]{0,10}?(\d{1,2})\s*月/,
    /通期\s*(\d{4})\/(\d{1,2})/,                    // "通期 2025/03"
  ];
  for (const p of fmPats) {
    const mm = text.match(p);
    if (mm) {
      // 最後のキャプチャグループを月として採用(どの regex でも末尾が月)
      const monthStr = mm[mm.length - 1];
      const v = parseInt(monthStr, 10);
      if (v >= 1 && v <= 12) { out.fiscal_month = v; break; }
    }
  }
  return out;
}

// ---------- Kabutan（配当利回り 4th フォールバック） ----------
async function scrapeKabutan(code) {
  const out = { yield: null, price: null };
  let html;
  try {
    html = await fetchText(`https://kabutan.jp/stock/?code=${code}`);
  } catch (e) {
    out._error = `kabutan: ${e.message}`;
    return out;
  }
  const text = stripHtml(html);
  // 「配当利回り X.XX %」
  const yPats = [
    /配当利回り\s*[（(]予[）)][^%]{0,30}?([\d.]+)\s*%/,
    /配当利回り[^%]{0,30}?([\d.]+)\s*%/,
  ];
  for (const p of yPats) {
    const mm = text.match(p);
    if (mm) { const v = toNum(mm[1]); if (inRange(v, 0, 20)) { out.yield = v; break; } }
  }
  // 株価フォールバック
  const pm = text.match(/株価[^0-9]{0,30}?([\d,]+(?:\.\d+)?)\s*円/);
  if (pm) { const v = toNum(pm[1]); if (inRange(v, 1, 500000)) out.price = v; }
  return out;
}

// ---------- みんかぶ（配当ページ） ----------
async function scrapeMinkabuDividend(code) {
  const out = { yield: null, payout_ratio: null, div_trend: null, per_share_div: null, kenri_month: null };
  let html;
  try {
    html = await fetchText(`https://minkabu.jp/stock/${code}/dividend`);
  } catch (e) {
    out._error = `minkabu: ${e.message}`;
    return out;
  }
  const pipe = stripToPipe(html);
  // 複数のパターンを試す(みんかぶHTMLは銘柄によって微妙に違う)
  const mkYieldPats = [
    /配当利回り\s*[（(]会社予想[）)]\|[^|]*?\|?\s*([\d.]+)\s*%/,
    /予想配当利回り\|[^|]*?\|?\s*([\d.]+)\s*%/,
    /配当利回り\|[^|]*?\|?\s*([\d.]+)\s*%/,
    /配当利回り[^0-9]{0,40}?([\d.]+)\s*%/,
  ];
  for (const p of mkYieldPats) {
    const mm = pipe.match(p);
    if (mm) { const v = toNum(mm[1]); if (inRange(v, 0, 20)) { out.yield = v; break; } }
  }
  // text 版からも試す(パイプ化でマッチ崩れた場合のフォールバック)
  if (out.yield == null) {
    const text = stripHtml(html);
    const mm = text.match(/配当利回り[^0-9]{0,40}?([\d.]+)\s*%/);
    if (mm) { const v = toNum(mm[1]); if (inRange(v, 0, 20)) out.yield = v; }
  }
  let m = pipe.match(/配当性向\|[^%]*?(\d+\.?\d*)%/);
  if (m) { const v = toNum(m[1]); if (inRange(v, 0, 500)) out.payout_ratio = v; }
  // 「株を100株買うと、年間 X,XXX円」から 1株配当を逆算（より直接的）
  {
    const textAll = stripHtml(html);
    const mm = textAll.match(/株を\s*(\d+)\s*株買うと[^0-9]{0,30}?年間[^0-9]{0,10}?([\d,]+)\s*円/);
    if (mm) {
      const nShares = parseInt(mm[1], 10);
      const totalYen = toNum(mm[2]);
      if (nShares > 0 && totalYen != null) {
        const perShare = totalYen / nShares;
        if (inRange(perShare, 0, 100000)) out.per_share_div = Math.round(perShare * 10) / 10;
      }
    }
  }
  // 配当権利確定月: 「配当権利確定月 3月」= 期末配当の権利確定月 → 支払は約3ヶ月後
  {
    const mm = pipe.match(/配当権利確定月\s*\|?\s*\|?\s*(\d{1,2})\s*月/);
    if (mm) { const v = parseInt(mm[1], 10); if (v >= 1 && v <= 12) out.kenri_month = v; }
  }
  // 増配・非減配実績
  let inc = html.match(/(\d+)\s*(?:期|年)\s*連続\s*増配/);
  if (inc) {
    out.div_trend = `${inc[1]}期連続増配`;
  } else if (html.includes('非減配')) {
    const nd = html.match(/(\d+)\s*(?:期|年)\s*連続\s*非減配/);
    if (nd) out.div_trend = `${nd[1]}期連続非減配`;
  }
  return out;
}

// ---------- みんかぶ（メインページ: 株価フォールバック） ----------
async function scrapeMinkabuPrice(code) {
  let html;
  try {
    html = await fetchText(`https://minkabu.jp/stock/${code}`);
  } catch (e) {
    return null;
  }
  const pipe = stripToPipe(html);
  // 「目標」を含まない文脈で 100〜500000 円の最初の値を採用
  const re = /([\d,]+(?:\.\d+)?)\|?\s*円/g;
  let m;
  while ((m = re.exec(pipe)) !== null) {
    const v = toNum(m[1]);
    if (!inRange(v, 100, 500000)) continue;
    const ctx = pipe.slice(Math.max(0, m.index - 30), m.index);
    if (!ctx.includes('目標')) return v;
  }
  return null;
}

// ---------- ETF/REIT 判定 ----------
function isEtfOrReit(code) {
  const n = parseInt(String(code).replace(/\D/g, ''), 10);
  if (!isFinite(n)) return false;
  if (n >= 1300 && n <= 2899) return true;
  if (n >= 8900 && n <= 8999) return true;
  return false;
}

// ---------- 既知 ETF の年間分配金テーブル ----------
// みんかぶ/Yahoo/kabutan から利回りが取れない ETF 向けの静的フォールバック。
// 配当月は 決算月(fiscal_months の末尾) 〜 決算月+2ヶ月で支払われる想定。
// 値は年1回ごとの最新値を参考にした概算。必要に応じて年1回メンテ。
const KNOWN_ETF_DIVIDENDS = {
  '1343': { per_share_div: 93,  fiscal_months: [2, 5, 8, 11], note: 'NEXT FUNDS 東証REIT指数 (Yahoo確認: 決算頻度4回)' },
  '1489': { per_share_div: 100, fiscal_months: [1, 4, 7, 10], note: 'NEXT FUNDS 日経平均高配当株50 (Yahoo確認: 決算頻度4回)' },
  '1478': { per_share_div: 80,  fiscal_months: [2, 8],        note: 'iシェアーズ MSCIジャパン高配当利回り (Yahoo確認: 決算頻度2回)' },
  '1577': { per_share_div: 110, fiscal_months: [1, 4, 7, 10], note: 'NEXT FUNDS 野村日本株高配当70 (Yahoo確認: 決算頻度4回)' },
  '1698': { per_share_div: 75,  fiscal_months: [1, 4, 7, 10], note: 'ダイワ上場投信-東証配当フォーカス100 (Yahoo確認: 決算頻度4回)' },
  '2564': { per_share_div: 130, fiscal_months: [1, 4, 7, 10], note: 'グローバルX MSCIスーパーディビィデンド-日本株式 (Yahoo確認: 決算頻度4回)' },
};

// ---------- メイン enrich 関数 ----------
async function enrichStock(code) {
  const info = {};
  const debug = {};
  const isEtf = isEtfOrReit(code);
  if (isEtf) info.is_etf = true;

  // 3 ソースを並列フェッチ
  const [yahoo, irbank, minkabu] = await Promise.all([
    scrapeYahoo(code).catch(e => ({ _error: e.message })),
    scrapeIrBank(code).catch(e => ({ _error: e.message })),
    scrapeMinkabuDividend(code).catch(e => ({ _error: e.message })),
  ]);
  debug.yahoo = yahoo._error || 'ok';
  debug.irbank = irbank._error || 'ok';
  debug.minkabu = minkabu._error || 'ok';

  // 株価: Yahoo 優先、フォールバックは minkabu メイン
  let price = yahoo.price;
  if (!price) price = await scrapeMinkabuPrice(code);
  if (price) info.cur_price = price;

  // PER 採用ロジック（server.py 準拠）
  const irPerAdopt = irbank.per_forecast || irbank.per_actual;
  if (yahoo.per_forecast && irPerAdopt) {
    info.per = yahoo.per_forecast;
    const diff = Math.abs(yahoo.per_forecast - irPerAdopt) / Math.min(yahoo.per_forecast, irPerAdopt);
    if (diff > 0.2) info.needs_review = true;
  } else if (yahoo.per_forecast) info.per = yahoo.per_forecast;
  else if (irbank.per_forecast) info.per = irbank.per_forecast;
  else if (irbank.per_actual) { info.per = irbank.per_actual; info.per_is_actual = true; }

  // PBR 採用ロジック
  if (yahoo.pbr_actual && irbank.pbr) {
    info.pbr = yahoo.pbr_actual;
    const diff = Math.abs(yahoo.pbr_actual - irbank.pbr) / Math.min(yahoo.pbr_actual, irbank.pbr);
    if (diff > 0.2) info.needs_review = true;
  } else if (yahoo.pbr_actual) info.pbr = yahoo.pbr_actual;
  else if (irbank.pbr) info.pbr = irbank.pbr;
  else if (yahoo.pbr_json) info.pbr = yahoo.pbr_json;

  if (info.per && info.pbr) info.mix_coef = Math.round(info.per * info.pbr * 100) / 100;

  // 配当利回り: みんかぶ優先 → Yahoo → Kabutan(4th fallback)
  let finalYield = minkabu.yield ?? yahoo.yield ?? null;
  let yieldSource = minkabu.yield != null ? 'minkabu' : (yahoo.yield != null ? 'yahoo' : null);
  if (finalYield == null && !isEtf) {
    // みんかぶ/Yahoo の両方で利回り取れず → Kabutan を試す
    try {
      const kabutan = await scrapeKabutan(code);
      debug.kabutan = kabutan._error || 'ok';
      if (kabutan.yield != null) { finalYield = kabutan.yield; yieldSource = 'kabutan'; }
      if (!price && kabutan.price) { price = kabutan.price; info.cur_price = price; }
    } catch (e) {
      debug.kabutan = `err:${e.message}`;
    }
  }
  if (finalYield != null) info.yield = finalYield;
  if (yieldSource) debug.yield_source = yieldSource;

  // 配当性向
  if (minkabu.payout_ratio != null) info.payout_ratio = minkabu.payout_ratio;

  // 増配実績
  if (minkabu.div_trend) info.div_trend = minkabu.div_trend;

  // 自己資本比率
  if (irbank.equity_ratio != null) info.equity_ratio = irbank.equity_ratio;

  // 決算月(カレンダー反映用)
  // 優先順: ① minkabu の配当権利確定月(実績ベース) → ② IR BANK の決算月から計算
  //   配当権利確定月 = 期末権利確定月 → 支払は約3ヶ月後 = end_month
  //   中間配当 = 期末の6ヶ月後
  //   例: 権利確定3月 → end_month=6, mid_month=12
  //       権利確定12月 → end_month=3, mid_month=9
  let endMonthHint = null, midMonthHint = null;
  if (minkabu.kenri_month != null) {
    // 権利確定月 + 3 = 支払月(期末)
    endMonthHint = ((minkabu.kenri_month + 3 - 1) % 12) + 1;
    midMonthHint = ((endMonthHint + 6 - 1) % 12) + 1;
    info.fiscal_year_end_month = minkabu.kenri_month; // 権利確定月=決算月が普通
    debug.month_source = 'minkabu_kenri';
  } else if (irbank.fiscal_month != null) {
    info.fiscal_year_end_month = irbank.fiscal_month;
    endMonthHint = ((irbank.fiscal_month + 3 - 1) % 12) + 1;
    midMonthHint = ((endMonthHint + 6 - 1) % 12) + 1;
    debug.month_source = 'irbank_fiscal';
  }
  if (endMonthHint) info.end_month_hint = endMonthHint;
  if (midMonthHint) info.mid_month_hint = midMonthHint;

  // 配当額算出:
  //   ① minkabu の 1株配当（直接取得）
  //   ② 株価 × 利回り（server.py と同じ計算）
  //   ③ ETF: 既知 ETF テーブルから分配金を引く（最終フォールバック）
  if (minkabu.per_share_div != null && minkabu.per_share_div > 0) {
    info.annual_div = minkabu.per_share_div;
    info.mid_div = Math.round(minkabu.per_share_div / 2 * 10) / 10;
    debug.div_source = 'minkabu_direct';
  } else if (price && finalYield) {
    info.annual_div = Math.round(price * finalYield / 100 * 10) / 10;
    info.mid_div = Math.round(info.annual_div / 2 * 10) / 10;
    debug.div_source = 'yield_price';
  } else if (isEtf && KNOWN_ETF_DIVIDENDS[code]) {
    // ETF フォールバック: 3サイトどれからも取れなかった場合のみ
    const etfData = KNOWN_ETF_DIVIDENDS[code];
    info.annual_div = etfData.per_share_div;
    info.mid_div = Math.round(etfData.per_share_div / etfData.fiscal_months.length * 10) / 10;
    debug.div_source = 'known_etf_table';
    debug.etf_note = etfData.note;
    if (price && etfData.per_share_div > 0) {
      info.yield = Math.round(etfData.per_share_div / price * 10000) / 100;
    }
    // ETF は年複数回分配があるので、末尾の決算月を end_month、もう1つを mid_month に
    if (etfData.fiscal_months.length >= 1) {
      const months = etfData.fiscal_months;
      info.end_month_hint = months[months.length - 1];
      if (months.length >= 2) info.mid_month_hint = months[0];
    }
  }

  info._debug = debug;
  return info;
}

// ---------- 軽量 price-only ----------
async function fetchPriceOnly(code) {
  const yahoo = await scrapeYahoo(code).catch(() => ({}));
  if (yahoo.price) return yahoo.price;
  const mk = await scrapeMinkabuPrice(code);
  return mk || null;
}

module.exports = {
  enrichStock,
  fetchPriceOnly,
  isEtfOrReit,
  // テスト/診断用
  scrapeYahoo,
  scrapeIrBank,
  scrapeMinkabuDividend,
  scrapeKabutan,
};
