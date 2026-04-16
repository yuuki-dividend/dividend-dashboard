// Vercel Serverless Function: /api/enrich_batch?codes=7203,8306,...
// Batch full enrichment (price + PER + PBR + yield + annual_div + ...).
// Returns {results: [{code, ...info}, ...]} — server-side parallelism bounded.

const { enrichStock } = require('./_lib/scrape');

const MAX_CODES = 20;          // fail-fast 10s/source × 3-4 sources → keep batch ≤ 20 to fit 60s budget
const CONCURRENCY = 8;         // 8 codes × 3-4 sources = 24-32 concurrent upstream fetches

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
      const info = await enrichStock(code);
      info.code = code;
      delete info._debug; /* クライアントに不要 */
      return info;
    } catch (e) {
      return { code, error: e.message };
    }
  }, CONCURRENCY);
  res.setHeader('Cache-Control', 's-maxage=300, stale-while-revalidate=3600');
  res.status(200).json({
    results,
    total: codes.length,
    elapsed_ms: Date.now() - started,
  });
};
