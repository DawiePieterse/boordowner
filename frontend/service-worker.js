// App-shell cache so the Owner View still renders (with its last data
// unavailable, but correctly styled) when the phone has no connection to
// the farm server. Data always goes over the network when available.
const CACHE_PREFIX = "boord-owner-";
const CACHE = "boord-owner-v46";
const REVALIDATE_TIMEOUT_MS = 10000;
const SHELL = [
  "./",
  "./index.html",
  "./owner.js",
  "./manifest.json",
  "./icons/icon-192.png",
  "./shared/styles.css",
  "./shared/api.js",
  "./shared/charts.js",
  "./shared/analysis-tab.js",
  "./shared/weather-tab.js",
  "./shared/risk-tab.js",
  "./shared/ai-panel.js",
  "./shared/estimate-tab.js",
  "./shared/market-tab.js",
  "./shared/ptr.js",
  "./shared/tailwind.css",
  "./shared/vendor/fontawesome/css/all.min.css",
  "./shared/vendor/fontawesome/webfonts/fa-solid-900.woff2",
  "./shared/vendor/html2canvas/html2canvas.min.js",
  "./shared/vendor/jspdf/jspdf.umd.min.js",
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
//
// The shell refresh below is ported from Boord's frontend/shared/sw-core.js.
// Keep the two the same.

// Absolute URLs for everything in SHELL, so the fetch handler can tell a shell
// request from an ordinary one. Resolved against this file's own location, so
// "./" is the app's page.
const SHELL_URLS = new Set(SHELL.map((path) => new URL(path, self.location.href).toString()));

let shellRefresh = null;

// Re-fetch the WHOLE shell and commit it in one go.
//
// Writing each revalidated file back on its own - which is what this used to
// do - can leave a device holding one release's index.html beside another
// release's owner.js. That is not a gentle degradation: the older JavaScript
// reaches for an element the newer HTML no longer has, throws before anything
// renders, and the screen paints white. It happened to Boord's Admin app on a
// farm server on 2026-09-04, and from the outside it was indistinguishable
// from the server being down.
//
// Nothing is committed unless every file arrives, so a refresh cut short by a
// dropped connection leaves the previous, consistent set in place rather than
// a half-updated one.
function refreshShell() {
  if (!shellRefresh) {
    const urls = [...SHELL_URLS];
    shellRefresh = Promise.all(urls.map((u) => fetch(u, { cache: "reload" }).catch(() => null)))
      .then(async (responses) => {
        if (responses.some((res) => !res || !res.ok)) return;
        const cache = await caches.open(CACHE);
        await Promise.all(responses.map((res, i) => cache.put(urls[i], res)));
      })
      .catch(() => { /* keep the shell that is already there */ })
      .then(() => { shellRefresh = null; });
  }
  return shellRefresh;
}

// Whether the copy just fetched is a different build from the cached one.
//
// Compared by validator rather than by body: this runs on every shell request,
// and reading two copies of owner.js to compare them would cost more than the
// caching saves. Starlette's StaticFiles sends both an ETag and a
// Last-Modified, so on this server the first check answers.
//
// When neither is available the answer is "unchanged", deliberately. Guessing
// "changed" would re-fetch the entire shell on every single request, which is
// a far worse failure than a stale one - and staleness is already visible,
// since the header prints the version.
async function shellFileChanged(cache, url, fresh) {
  const cached = await cache.match(url);
  if (!cached) return true;
  for (const header of ["etag", "last-modified", "content-length"]) {
    const before = cached.headers.get(header);
    const after = fresh.headers.get(header);
    if (before && after) return before !== after;
  }
  return false;
}

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (url.pathname.startsWith("/api/")) return; // never cache API calls
  if (event.request.method !== "GET" || url.origin !== self.location.origin) return;

  // Page loads are cached by path only: a cache lookup matches the query
  // string too, so any link carrying one would miss the stored shell and
  // fail offline. It also stops one cache entry piling up per distinct link.
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
  // A shell file is looked up by URL either way; only the key differs.
  const shellUrl = isPageLoad ? cacheKey : url.href;
  const isShell = SHELL_URLS.has(shellUrl);

  const revalidateAbort = new AbortController();
  const revalidateTimer = setTimeout(() => revalidateAbort.abort(), REVALIDATE_TIMEOUT_MS);
  const update = fetch(event.request, { signal: revalidateAbort.signal })
    .then(async (res) => {
      if (res.ok) {
        const cache = await caches.open(CACHE);
        if (isShell) {
          // One shell file moving means the whole shell moved. Refresh them
          // together or not at all - see refreshShell above for why.
          if (await shellFileChanged(cache, shellUrl, res)) await refreshShell();
        } else {
          await cache.put(cacheKey, res.clone());
        }
      }
      return res;
    })
    .catch(() => null) // offline: the cached copy below is the answer
    .finally(() => clearTimeout(revalidateTimer));
  event.waitUntil(update);

  // Matched against THIS app's cache, not the global caches.match(), which
  // searches every cache on the origin. This app is alone on its origin
  // (port 8010) today, but a leftover cache from an older install, or another
  // app published on the same host and port later, would otherwise be free to
  // answer with its own stale copy of a file such as shared/api.js.
  event.respondWith(
    caches.open(CACHE)
      .then((cache) => cache.match(cacheKey))
      .then((cached) => cached || update.then((res) => res || Response.error()))
  );
});
