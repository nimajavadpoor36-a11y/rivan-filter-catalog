// Makes the site installable and lets the page shell open without a connection.
// Search results always come from the server, so searching needs a connection.
const CACHE = 'filterfinder-shell-v1';
const SHELL = ['/', '/static/css/app.css', '/static/js/app.js'];

self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});
self.addEventListener('activate', (e) => {
  e.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))).then(() => self.clients.claim()));
});
self.addEventListener('fetch', (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== 'GET' || url.origin !== location.origin) return;
  if (url.pathname.startsWith('/api/') || url.pathname.startsWith('/admin')) return; // never cache data or admin
  e.respondWith(
    fetch(e.request).then((res) => {
      if (res.ok && (url.pathname === '/' || url.pathname.startsWith('/static/') || url.pathname.startsWith('/uploads/'))) {
        const copy = res.clone();
        caches.open(CACHE).then((c) => c.put(e.request, copy));
      }
      return res;
    }).catch(() => caches.match(e.request))
  );
});
