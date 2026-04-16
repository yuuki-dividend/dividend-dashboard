// Vercel Serverless Function: /api/enrich?code=7203
// Server-side scraping — no CORS proxy needed.
// Response: JSON with { code, cur_price, per, pbr, yield, annual_div, payout_ratio, ... }

const { enrichStock } = require('./_lib/scrape');

module.exports = async (req, res) => {
  // Basic CORS (same-origin usually, but allow for flexibility)
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, OPTIONS');
  if (req.method === 'OPTIONS') { res.status(204).end(); return; }

  const rawCode = (req.query?.code || '').toString().trim();
  if (!/^\d{3,5}$/.test(rawCode)) {
    res.status(400).json({ error: 'invalid code', detail: 'code must be 3-5 digits' });
    return;
  }
  const code = rawCode;
  const started = Date.now();
  try {
    const info = await enrichStock(code);
    info.code = code;
    info._elapsed_ms = Date.now() - started;
    // Cache at the edge for 1 hour (browsers still hit us unless they have their own cache)
    res.setHeader('Cache-Control', 's-maxage=3600, stale-while-revalidate=86400');
    res.status(200).json(info);
  } catch (e) {
    res.status(500).json({ code, error: 'enrich failed', detail: e.message, _elapsed_ms: Date.now() - started });
  }
};
