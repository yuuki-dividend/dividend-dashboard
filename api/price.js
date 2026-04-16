// Vercel Serverless Function: /api/price?code=7203
// Lightweight price-only fetch (Yahoo primary, minkabu fallback).
// Response: { code, price, source }

const { fetchPriceOnly } = require('./_lib/scrape');

module.exports = async (req, res) => {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, OPTIONS');
  if (req.method === 'OPTIONS') { res.status(204).end(); return; }

  const rawCode = (req.query?.code || '').toString().trim();
  if (!/^\d{3,5}$/.test(rawCode)) {
    res.status(400).json({ error: 'invalid code' });
    return;
  }
  const code = rawCode;
  const started = Date.now();
  try {
    const price = await fetchPriceOnly(code);
    if (price == null) {
      res.status(200).json({ code, price: null, _elapsed_ms: Date.now() - started });
      return;
    }
    // Short edge cache — prices change intraday
    res.setHeader('Cache-Control', 's-maxage=60, stale-while-revalidate=300');
    res.status(200).json({ code, price, _elapsed_ms: Date.now() - started });
  } catch (e) {
    res.status(500).json({ code, error: 'price fetch failed', detail: e.message });
  }
};
