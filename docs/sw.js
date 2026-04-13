// Service Worker for 高配当株ダッシュボード PWA (Static/GitHub Pages version)
const CACHE_VERSION = 'v1';
const CACHE_NAME = 'dividend-dashboard-static-' + CACHE_VERSION;

// Resources to pre-cache on install
const PRECACHE_URLS = [
  './',
  './manifest.json',
  './icon-192.svg',
  './icon-512.svg',
  'https://cdn.jsdelivr.net/npm/chart.js'
];

// Data files to cache (network-first)
const DATA_FILES = [
  'stocks.json',
  'screening_data.json',
  'all_stocks.json'
];

// Install: pre-cache static resources
self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE_NAME).then(cache => {
      console.log('[SW-Static] Pre-caching static resources');
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
              console.log('[SW-Static] Deleting old cache:', key);
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

  // Data files: network first, cache response
  if (DATA_FILES.some(f => url.pathname.endsWith(f))) {
    event.respondWith(networkFirstData(event.request));
    return;
  }

  // Static resources: network first with cache fallback
  event.respondWith(networkFirstStatic(event.request));
});

async function networkFirstData(request) {
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
    return new Response(JSON.stringify([]), {
      status: 200,
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
