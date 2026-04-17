// Vercel Serverless Function: /api/compare?url=<encoded-url>
// 4サイト比較モーダルのための HTML プロキシ。
// クライアント側で外部 (corsproxy.io) を使うと 403 を食らうため、
// Vercel 経由で一度取得して返す。ホワイトリスト外のURLは拒否。

const ALLOWED_HOSTS = [
  'finance.yahoo.co.jp',
  'irbank.net',
  'minkabu.jp',
  'kabutan.jp',
];

const UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36';

module.exports = async (req, res) => {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, OPTIONS');
  if (req.method === 'OPTIONS') { res.status(204).end(); return; }

  const target = (req.query?.url || '').toString();
  if (!target) { res.status(400).json({ error: 'url required' }); return; }

  let u;
  try { u = new URL(target); } catch { res.status(400).json({ error: 'invalid url' }); return; }
  if (!ALLOWED_HOSTS.some(d => u.hostname === d || u.hostname.endsWith('.' + d))) {
    res.status(403).json({ error: 'domain not allowed', host: u.hostname });
    return;
  }

  try {
    const r = await fetch(target, {
      headers: { 'User-Agent': UA, 'Accept-Language': 'ja,en;q=0.8' },
      signal: AbortSignal.timeout(10000),
    });
    const text = await r.text();
    res.setHeader('Content-Type', 'text/html; charset=utf-8');
    res.setHeader('Cache-Control', 's-maxage=60, stale-while-revalidate=300');
    res.status(r.status).send(text);
  } catch (e) {
    res.status(502).json({ error: e.message });
  }
};
