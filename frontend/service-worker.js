// App-shell cache so the Owner View still renders (with its last data
// unavailable, but correctly styled) when the phone has no connection to
// the farm server. Data always goes over the network when available.
const CACHE_PREFIX = "boord-owner-";
const CACHE = "boord-owner-v18";
const REVALIDATE_TIMEOUT_MS = 10000;
const SHELL = [
  "./",
  "./index.html",
  "./owner.js",
  "../shared/styles.css",
  "../shared/api.js",
  "../shared/charts.js",
  "../shared/analysis-tab.js",
  "../shared/weather-tab.js",
  "../shared/risk-tab.js",
  "../shared/ptr.js",
  "../shared/tailwind.js",
  "../shared/vendor/fontawesome/css/all.min.css",
  "../shared/vendor/fontawesome/webfonts/fa-solid-900.woff2",
  "../shared/vendor/html2canvas/html2canvas.min.js",
  "../shared/vendor/jspdf/jspdf.umd.min.js",
];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(SHELL)));
  self.skipWaiting();
});

// Drop only THIS screen's older caches. CacheStorage is shared per-origin,
// so deleting every non-matching key would wipe the other screens' offline
// shells the moment someone opens two of the apps on the same device.
self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) => Promise.all(
      keys.filter((k) => k.startsWith(CACHE_PREFIX) && k !== CACHE).map((k) => caches.delete(k))
    ))
  );
  self.clients.claim();
});

// Stale-while-revalidate: answer instantly from cache (so a device with no
// signal still gets the whole UI), but refresh the cached copy in the
// background whenever the server IS reachable. Without the revalidate half,
// a cache-first worker pins a device to old JS until the CACHE name changes.
self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (url.pathname.startsWith("/api/")) return; // never cache API calls
  if (event.request.method !== "GET" || url.origin !== self.location.origin) return;

  // Page loads are cached by path only. The Owner View is opened as
  // /owner/?key=..., and a cache lookup matches the query string too - so
  // without this the shell would never be found and the page would fail
  // offline. It also stops one cache entry piling up per distinct link.
  const isPageLoad = event.request.mode === "navigate";
  const cacheKey = isPageLoad ? url.origin + url.pathname : event.request;

  // Two things the revalidation needs to behave. It is registered with
  // waitUntil, so the browser keeps this worker alive until the new copy is
  // actually written - otherwise the worker can be shut down the moment the
  // cached response is handed back, the write never lands, and devices stay
  // pinned to old code, the exact failure this strategy exists to prevent.
  // And it carries a deadline, because on an unreachable network these
  // background fetches never settle, and a browser allows only a handful of
  // connections per host - uncapped they pile up and starve the app's own
  // API requests of sockets.
  const revalidateAbort = new AbortController();
  const revalidateTimer = setTimeout(() => revalidateAbort.abort(), REVALIDATE_TIMEOUT_MS);
  const update = fetch(event.request, { signal: revalidateAbort.signal })
    .then(async (res) => {
      if (res.ok) {
        const cache = await caches.open(CACHE);
        await cache.put(cacheKey, res.clone());
      }
      return res;
    })
    .catch(() => null) // offline: the cached copy below is the answer
    .finally(() => clearTimeout(revalidateTimer));
  event.waitUntil(update);

  // Matched against THIS screen's cache, not the global caches.match(), which
  // searches every cache on the origin and would happily answer with another
  // screen's stale copy of a shared file (all four cache shared/api.js).
  event.respondWith(
    caches.open(CACHE)
      .then((cache) => cache.match(cacheKey))
      .then((cached) => cached || update.then((res) => res || Response.error()))
  );
});
