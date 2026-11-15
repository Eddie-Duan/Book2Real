/* Service worker for 纸间生灵 · 互动绘本. No build step, no dependencies.
 *
 * Strategies (docs/INTEGRATION_CONTRACT.md §3):
 *   navigation      network-first, fall back to the cached shell
 *   /api/...        network-first, store a copy when the network answers 200
 *   anything else   cache-first, then network (and keep a copy)
 *
 * `CACHE_V` is the single version switch: bump it and the old caches are
 * dropped on activate. Precache is per-item best effort — one 404 (an older
 * server that does not serve a module yet) logs a warning and never blocks
 * install.
 */

const CACHE_V = "b2r-v1";                 // keep in sync with offline.js
const CACHE_NAME = `b2r-cache-${CACHE_V}`;

/* the required shell: the page and the two files it cannot run without */
const SHELL = ["/", "/static/reader.css", "/static/reader.js"];

/* optional: modules and metadata. A 404 here is a warning, never a failure. */
const SHELL_OPTIONAL = [
  "/static/qa.js", "/static/voice.js", "/static/tts.js", "/static/offline.js",
  "/static/manifest.json", "/static/reader.html",
];

self.addEventListener("install", (event) => {
  event.waitUntil((async () => {
    const cache = await caches.open(CACHE_NAME);
    await Promise.all(SHELL.map(async (url) => {
      try {
        const res = await fetch(new Request(url, { cache: "reload" }));
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        await cache.put(url, res.clone());
      } catch (err) {
        console.warn(`[b2r sw] precache skipped ${url}: ${err && err.message}`);
      }
    }));
    await Promise.all(SHELL_OPTIONAL.map(async (url) => {
      try {
        const res = await fetch(new Request(url, { cache: "reload" }));
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        await cache.put(url, res.clone());
      } catch (err) {
        console.warn(`[b2r sw] optional asset skipped ${url}: ${err && err.message}`);
      }
    }));
    await self.skipWaiting();
  })());
});

self.addEventListener("activate", (event) => {
  event.waitUntil((async () => {
    const keys = await caches.keys();
    await Promise.all(keys.map(key => {
      if (key !== CACHE_NAME && key.indexOf("b2r-cache-") === 0) return caches.delete(key);
      return null;
    }));
    await self.clients.claim();
  })());
});

function storeFrom(request, response) {
  if (!response || response.status !== 200 || response.type === "opaque") return response;
  const copy = response.clone();
  caches.open(CACHE_NAME).then(cache => cache.put(request, copy)).catch(() => {});
  return response;
}

async function networkFirst(request, fallbackUrl) {
  try {
    const res = await fetch(request);
    if (res && res.ok) return storeFrom(request, res);
    return res;
  } catch (_) {
    const cache = await caches.open(CACHE_NAME);
    const hit = await cache.match(request, { ignoreSearch: true });
    if (hit) return hit;
    if (fallbackUrl) {
      const shell = await cache.match(fallbackUrl);
      if (shell) return shell;
    }
    return new Response(JSON.stringify({ error: "offline", offline: true }), {
      status: 503,
      headers: { "Content-Type": "application/json; charset=utf-8" },
    });
  }
}

async function cacheFirst(request) {
  const cache = await caches.open(CACHE_NAME);
  const hit = await cache.match(request, { ignoreSearch: false });
  if (hit) return hit;
  try {
    const res = await fetch(request);
    if (res && res.ok) return storeFrom(request, res);
    return res;
  } catch (err) {
    return new Response("", { status: 504, statusText: "offline" });
  }
}

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") return;
  let url;
  try { url = new URL(request.url); } catch (_) { return; }
  if (url.origin !== self.location.origin) return;

  if (request.mode === "navigate") {
    event.respondWith(networkFirst(request, "/"));
    return;
  }
  if (url.pathname.indexOf("/api/") === 0) {
    event.respondWith(networkFirst(request));
    return;
  }
  event.respondWith(cacheFirst(request));
});
