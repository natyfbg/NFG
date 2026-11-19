/* Qiltsim service worker (served by pwa.py at /sw.js, versioned per deploy).
   Pages: network first, a copy kept for offline use. Static files: kept, served from the
   phone. Never kept: account, admin and sign-in pages. Signing out clears kept pages. */
"use strict";
{% if not enabled %}
/* PWA_ENABLED=0: delete everything this worker kept and step aside. */
self.addEventListener("install", function () { self.skipWaiting(); });
self.addEventListener("activate", function (event) {
  event.waitUntil(
    caches.keys()
      .then(function (keys) { return Promise.all(keys.filter(function (k) { return k.indexOf("qiltsim-") === 0; }).map(function (k) { return caches.delete(k); })); })
      .then(function () { return self.registration.unregister(); })
  );
});
{% else %}
var VERSION = {{ version|tojson }};
var SHELL = "qiltsim-shell-" + VERSION;
var PAGES = "qiltsim-pages";
var FILES = "qiltsim-files";
var OFFLINE_URL = {{ offline_url|tojson }};
var SHELL_URLS = {{ shell|tojson }};
var NO_KEEP = {{ no_keep|tojson }};
var CLEAR_ON = {{ clear_on|tojson }};
var MAX_PAGES = {{ max_pages }};
var MAX_FILES = {{ max_files }};
var CDN = "https://cdn.jsdelivr.net/";

self.addEventListener("install", function (event) {
  event.waitUntil(
    caches.open(SHELL)
      .then(function (cache) { return cache.addAll(SHELL_URLS); })
      .then(function () { return self.skipWaiting(); })
  );
});

self.addEventListener("activate", function (event) {
  event.waitUntil(
    caches.keys()
      .then(function (keys) {
        return Promise.all(keys.filter(function (k) {
          return k.indexOf("qiltsim-shell-") === 0 && k !== SHELL;
        }).map(function (k) { return caches.delete(k); }));
      })
      .then(function () { return self.clients.claim(); })
  );
});

function startsWithAny(path, list) {
  for (var i = 0; i < list.length; i++) {
    if (path === list[i] || path.indexOf(list[i] + "/") === 0 || path.indexOf(list[i] + "?") === 0) return true;
  }
  return false;
}

function trim(name, max) {
  return caches.open(name).then(function (cache) {
    return cache.keys().then(function (keys) {
      var extra = keys.length - max;
      if (extra <= 0) return;
      return Promise.all(keys.slice(0, extra).map(function (k) { return cache.delete(k); }));
    });
  });
}

function offlinePage() {
  return caches.match(OFFLINE_URL).then(function (r) {
    return r || new Response("You're offline.", { status: 503, headers: { "Content-Type": "text/plain; charset=utf-8" } });
  });
}

function keepable(response) {
  if (!response || response.status !== 200 || response.type !== "basic" || response.redirected) return false;
  var cc = response.headers.get("Cache-Control") || "";
  return cc.indexOf("no-store") === -1 && (response.headers.get("Content-Type") || "").indexOf("text/html") === 0;
}

/* Pages: try the network; keep a copy; with no signal, the kept copy or the offline page. */
function page(event, url) {
  return fetch(event.request)
    .then(function (response) {
      if (keepable(response) && !startsWithAny(url.pathname, NO_KEEP)) {
        var copy = response.clone();
        event.waitUntil(
          caches.open(PAGES)
            .then(function (cache) { return cache.put(event.request, copy); })
            .then(function () { return trim(PAGES, MAX_PAGES); })
        );
      }
      return response;
    })
    .catch(function () {
      return caches.match(event.request, { cacheName: PAGES }).then(function (kept) { return kept || offlinePage(); });
    });
}

/* Static files and Bootstrap: from the phone when kept, else fetch and keep. Versioned
   links (?v=) change on every deploy, so a kept file is never stale for long. */
function file(event) {
  return caches.match(event.request).then(function (kept) {
    if (kept) return kept;
    return fetch(event.request).then(function (response) {
      if (response && response.status === 200 && (response.type === "basic" || response.type === "cors")) {
        var copy = response.clone();
        event.waitUntil(
          caches.open(FILES)
            .then(function (cache) { return cache.put(event.request, copy); })
            .then(function () { return trim(FILES, MAX_FILES); })
        );
      }
      return response;
    });
  });
}

self.addEventListener("fetch", function (event) {
  var req = event.request;
  var url = new URL(req.url);
  var sameOrigin = url.origin === self.location.origin;

  if (req.mode === "navigate" && req.method === "POST") {
    if (sameOrigin && startsWithAny(url.pathname, CLEAR_ON)) {
      event.waitUntil(caches.delete(PAGES));
    }
    /* A form sent with no signal: the offline page instead of the browser's error. */
    event.respondWith(fetch(req).catch(offlinePage));
    return;
  }
  if (req.method !== "GET") return;

  if (req.mode === "navigate" && sameOrigin) {
    event.respondWith(page(event, url));
    return;
  }
  if ((sameOrigin && url.pathname.indexOf("/static/") === 0) || req.url.indexOf(CDN) === 0) {
    event.respondWith(file(event));
  }
  /* Everything else (live counts, JSON) goes to the network as usual. */
});
{% endif %}
