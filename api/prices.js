// Vercel Serverless Function: /api/prices?codes=7203,8306,9432,...
// Batch price fetch — one HTTP round-trip for many stocks.
// Concurrency is limited server-side to avoid hammering Yahoo.
// Response: { results: [{code, price}, ...], elapsed_ms }

const { fetchPriceOnly } = require('./_lib/scrape');

const MAX_CODES = 80;          // safety cap per request
const CONCURRENCY = 10;        // parallel fetches to Yahoo

async function runWithConcurrency(items, worker, concurrency) {
  const results = new Array(items.length);
  let next = 0;
  async function loop() {
    while (true) {
      const idx = next++;
      if (idx >= items.length) return;
      try { results[idx] = await worker(items[idx], idx); }
      catch (e) { results[idx] = { error: e.message }; }
    }
  }
  await Promise.all(Array.from({ length: Math.min(concurrency, items.length) }, () => loop()));
  return results;
}

module.exports = async (req, res) => {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, OPTIONS');
  if (req.method === 'OPTIONS') { res.status(204).end(); return; }

  const raw = (req.query?.codes || '').toString().trim();
  if (!raw) { res.status(400).json({ error: 'codes required' }); return; }
  const codes = raw.split(',').map(c => c.trim()).filter(c => /^\d{3,5}$/.test(c));
  if (!codes.length) { res.status(400).json({ error: 'no valid codes' }); return; }
  if (codes.length > MAX_CODES) {
    res.status(400).json({ error: `too many codes (max ${MAX_CODES})`, got: codes.length });
    return;
  }
  const started = Date.now();
  const results = await runWithConcurrency(codes, async (code) => {
    try {
      const price = await fetchPriceOnly(code);
      return { code, price };
    } catch (e) {
      return { code, price: null, error: e.message };
    }
  }, CONCURRENCY);
  res.setHeader('Cache-Control', 's-maxage=60, stale-while-revalidate=300');
  res.status(200).json({
    results,
    total: codes.length,
    succeeded: results.filter(r => r?.price != null).length,
    failed: results.filter(r => r?.price == null).length,
    elapsed_ms: Date.now() - started,
  });
};
