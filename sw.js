// Service Worker for 高配当株ダッシュボード PWA
const CACHE_VERSION = 'v1';
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
      return cache.addAll(PRECACHE_URLS);
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

// Fetch: Network First with Cache Fallback
self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);

  // Only handle GET requests
  if (event.request.method !== 'GET') return;

  // API requests: network first, cache response with timestamp
  if (API_URLS.some(api => url.pathname === api)) {
    event.respondWith(networkFirstAPI(event.request));
    return;
  }

  // Static resources: network first with cache fallback
  event.respondWith(networkFirstStatic(event.request));
});

async function networkFirstAPI(request) {
  const cache = await caches.open(CACHE_NAME);
  try {
    const networkResponse = await fetch(request);
    if (networkResponse.ok) {
      // Clone the response and add a cache timestamp header
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
      // Return a fresh response (without cache headers)
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
    // Offline: serve from cache with X-From-Cache header
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

async function networkFirstStatic(request) {
  const cache = await caches.open(CACHE_NAME);
  try {
    const networkResponse = await fetch(request);
    if (networkResponse.ok) {
      cache.put(request, networkResponse.clone());
    }
    return networkResponse;
  } catch (err) {
    const cachedResponse = await cache.match(request);
    if (cachedResponse) {
      return cachedResponse;
    }
    // For HTML pages, return a basic offline page
    if (request.headers.get('Accept') && request.headers.get('Accept').includes('text/html')) {
      return new Response(
        '<html><body style="background:#1e293b;color:#f0ece8;display:flex;justify-content:center;align-items:center;height:100vh;font-family:system-ui"><div style="text-align:center"><h1>Offline</h1><p>No cached version available.</p></div></body></html>',
        { status: 503, headers: { 'Content-Type': 'text/html' } }
      );
    }
    return new Response('Offline', { status: 503 });
  }
}
