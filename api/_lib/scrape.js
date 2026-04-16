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
async function fetchText(url, timeoutMs = 15000, retries = 1) {
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
        await new Promise(r => setTimeout(r, 800));
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
  const out = { per_forecast: null, per_actual: null, pbr: null, equity_ratio: null };
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
  return out;
}

// ---------- みんかぶ（配当ページ） ----------
async function scrapeMinkabuDividend(code) {
  const out = { yield: null, payout_ratio: null, div_trend: null };
  let html;
  try {
    html = await fetchText(`https://minkabu.jp/stock/${code}/dividend`);
  } catch (e) {
    out._error = `minkabu: ${e.message}`;
    return out;
  }
  const pipe = stripToPipe(html);
  let m = pipe.match(/配当利回り\|[^|]*?\|?\s*([\d.]+)\s*%/);
  if (m) { const v = toNum(m[1]); if (inRange(v, 0, 20)) out.yield = v; }
  m = pipe.match(/配当性向\|[^%]*?(\d+\.?\d*)%/);
  if (m) { const v = toNum(m[1]); if (inRange(v, 0, 500)) out.payout_ratio = v; }
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

  // 配当利回り: みんかぶ優先、Yahoo フォールバック
  const finalYield = minkabu.yield ?? yahoo.yield ?? null;
  if (finalYield != null) info.yield = finalYield;

  // 配当性向
  if (minkabu.payout_ratio != null) info.payout_ratio = minkabu.payout_ratio;

  // 増配実績
  if (minkabu.div_trend) info.div_trend = minkabu.div_trend;

  // 自己資本比率
  if (irbank.equity_ratio != null) info.equity_ratio = irbank.equity_ratio;

  // 配当額算出: 株価 × 利回り（server.py と同じ）
  if (price && finalYield) {
    info.annual_div = Math.round(price * finalYield / 100 * 10) / 10;
    info.mid_div = Math.round(info.annual_div / 2 * 10) / 10;
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
};
