// Service Worker for 高配当株ダッシュボード PWA
// v3: stale-while-revalidate (same-origin + CDN only) — CORS proxy calls bypass SW
const CACHE_VERSION = 'v3';
const CACHE_NAME = 'dividend-dashboard-' + CACHE_VERSION;

// Resources to pre-cache on install
const PRECACHE_URLS = [
  '/',
  '/manifest.json',
  '/icon-192.svg',
  '/icon-512.svg',
  'https://cdn.jsdelivr.net/npm/chart.js'
];

// API endpoints to cache (network-first)
const API_URLS = [
  '/api/stocks',
  '/api/screening',
  '/api/all_stocks'
];

// Install: pre-cache static resources
self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE_NAME).then(cache => {
      console.log('[SW] Pre-caching static resources');
      // Use addAll but tolerate individual failures (CDN might be blocked)
      return Promise.all(
        PRECACHE_URLS.map(url =>
          cache.add(url).catch(err => console.warn('[SW] Precache failed:', url, err))
        )
      );
    }).then(() => self.skipWaiting())
  );
});

// Activate: clean up old caches
self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(keys => {
      return Promise.all(
        keys.filter(key => key.startsWith('dividend-dashboard-') && key !== CACHE_NAME)
            .map(key => {
              console.log('[SW] Deleting old cache:', key);
              return caches.delete(key);
            })
      );
    }).then(() => self.clients.claim())
  );
});

// Fetch: Stale-While-Revalidate for static, Network First for API.
// IMPORTANT: We only intercept same-origin + whitelisted CDN. CORS proxy calls
// (corsproxy.io / allorigins / codetabs / etc.) are NOT touched — they must go
// direct to network so rate-limit / cache-bypass behavior stays correct.
const SW_SAME_ORIGIN_OR_CDN = new Set([
  self.location.origin,
  'https://cdn.jsdelivr.net',
]);
self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);

  // Only handle GET requests
  if (event.request.method !== 'GET') return;

  // Skip non-http(s) requests (chrome-extension:, data:, etc.)
  if (!url.protocol.startsWith('http')) return;

  // Do NOT intercept cross-origin fetches (CORS proxies, third-party APIs, etc.)
  // These must reach the network directly every time.
  if (!SW_SAME_ORIGIN_OR_CDN.has(url.origin)) return;

  // API requests: network first
  if (API_URLS.some(api => url.pathname === api)) {
    event.respondWith(networkFirstAPI(event.request));
    return;
  }

  // Static resources (HTML, CSS, JS, images, icons, whitelisted CDN): stale-while-revalidate
  event.respondWith(staleWhileRevalidate(event.request, event));
});

// Listen for skipWaiting message (triggered by update banner "再読込" button)
self.addEventListener('message', event => {
  if (event.data && event.data.type === 'SKIP_WAITING') {
    self.skipWaiting();
  }
});

// --- Strategies ---

async function networkFirstAPI(request) {
  const cache = await caches.open(CACHE_NAME);
  try {
    const networkResponse = await fetch(request);
    if (networkResponse.ok) {
      const body = await networkResponse.clone().text();
      const now = new Date().toISOString();
      const cachedResponse = new Response(body, {
        status: networkResponse.status,
        statusText: networkResponse.statusText,
        headers: {
          'Content-Type': networkResponse.headers.get('Content-Type') || 'application/json',
          'X-Cache-Timestamp': now,
          'X-From-Cache': 'false'
        }
      });
      await cache.put(request, cachedResponse.clone());
      return new Response(body, {
        status: networkResponse.status,
        statusText: networkResponse.statusText,
        headers: {
          'Content-Type': networkResponse.headers.get('Content-Type') || 'application/json',
          'X-Cache-Timestamp': now,
          'X-From-Cache': 'false'
        }
      });
    }
    return networkResponse;
  } catch (err) {
    const cachedResponse = await cache.match(request);
    if (cachedResponse) {
      const body = await cachedResponse.text();
      const timestamp = cachedResponse.headers.get('X-Cache-Timestamp') || '';
      return new Response(body, {
        status: 200,
        statusText: 'OK',
        headers: {
          'Content-Type': 'application/json; charset=utf-8',
          'X-Cache-Timestamp': timestamp,
          'X-From-Cache': 'true'
        }
      });
    }
    return new Response(JSON.stringify({ error: 'offline', message: 'No cached data available' }), {
      status: 503,
      headers: { 'Content-Type': 'application/json' }
    });
  }
}

/**
 * Stale-While-Revalidate:
 *  - Serve from cache immediately (if available) → instant load
 *  - In parallel, fetch from network → update cache
 *  - If the network response differs from cached (by ETag / content-length / text hash),
 *    post a 'NEW_VERSION' message to all clients so they can show an update banner.
 */
async function staleWhileRevalidate(request, fetchEvent) {
  const cache = await caches.open(CACHE_NAME);
  const cachedResponse = await cache.match(request);

  // Background network fetch (non-blocking from cache hit's perspective)
  const networkFetch = fetch(request).then(async (networkResponse) => {
    if (!networkResponse || !networkResponse.ok) {
      return networkResponse;
    }
    try {
      // Clone for cache storage
      const networkClone = networkResponse.clone();

      // Detect version change for HTML documents only (avoid spamming for every asset)
      const isHTML = isHTMLRequest(request, networkResponse);
      if (isHTML && cachedResponse) {
        const newerText = await networkResponse.clone().text();
        const olderText = await cachedResponse.clone().text();
        if (newerText !== olderText) {
          notifyClients({ type: 'NEW_VERSION', url: request.url });
        }
      }
      await cache.put(request, networkClone);
    } catch (err) {
      console.warn('[SW] Cache update failed:', request.url, err);
    }
    return networkResponse;
  }).catch(err => {
    // Network failed — cached response (if any) will still be served
    console.warn('[SW] Network fetch failed:', request.url, err);
    return null;
  });

  // Return cache immediately if present; otherwise wait for network
  if (cachedResponse) {
    // Keep SW alive until the background refresh finishes (waitUntil accepts the promise)
    if (fetchEvent && typeof fetchEvent.waitUntil === 'function') {
      try { fetchEvent.waitUntil(networkFetch); } catch (_) {}
    } else {
      networkFetch.catch(() => {});
    }
    return cachedResponse;
  }

  // No cache → await network (fallback to offline page for HTML)
  const networkResponse = await networkFetch;
  if (networkResponse) return networkResponse;

  if (isHTMLRequest(request)) {
    return new Response(
      '<html><body style="background:#1e293b;color:#f0ece8;display:flex;justify-content:center;align-items:center;height:100vh;font-family:system-ui"><div style="text-align:center"><h1>Offline</h1><p>No cached version available.</p></div></body></html>',
      { status: 503, headers: { 'Content-Type': 'text/html' } }
    );
  }
  return new Response('Offline', { status: 503 });
}

function isHTMLRequest(request, response) {
  if (response) {
    const ct = response.headers.get('Content-Type') || '';
    if (ct.includes('text/html')) return true;
  }
  const accept = request.headers.get('Accept') || '';
  if (accept.includes('text/html')) return true;
  const url = new URL(request.url);
  if (url.pathname === '/' || url.pathname.endsWith('/') || url.pathname.endsWith('.html')) return true;
  return false;
}

async function notifyClients(message) {
  try {
    const clientsList = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
    for (const client of clientsList) {
      client.postMessage(message);
    }
  } catch (err) {
    console.warn('[SW] notifyClients failed:', err);
  }
}

