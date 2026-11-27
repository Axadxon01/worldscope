/* WorldScope service worker: the app on a phone's home screen, pages that open without a network,
   and the browser's own notifications. Version __VERSION__ — a new deploy replaces the old caches. */
const VERSION = "__VERSION__", STATIC = "ws-static-" + VERSION, PAGES = "ws-pages-" + VERSION;
const PRECACHE = ["/offline", "/static/style.css", "/static/ui.js", "/static/icon-192.png"];

self.addEventListener("install", e => {
  e.waitUntil(caches.open(STATIC).then(c => c.addAll(PRECACHE)).then(() => self.skipWaiting()));
});
self.addEventListener("activate", e => {
  e.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(k => k !== STATIC && k !== PAGES).map(k => caches.delete(k))))
    .then(() => self.clients.claim()));
});
self.addEventListener("fetch", e => {
  const req = e.request, url = new URL(req.url);
  if (req.method !== "GET" || url.origin !== location.origin) return;
  if (url.pathname.startsWith("/static/")) {          // files: the cache first, they carry a version
    e.respondWith(caches.match(req).then(hit => hit || fetch(req).then(res => {
      if (res.ok) { const copy = res.clone(); caches.open(STATIC).then(c => c.put(req, copy)); }
      return res;
    })));
    return;
  }
  if (req.mode === "navigate") {                       // pages: the network first, the last copy when offline
    e.respondWith(fetch(req).then(res => {
      if (res.ok && !url.pathname.startsWith(("/admin"))) { const copy = res.clone(); caches.open(PAGES).then(c => c.put(req, copy)); }
      return res;
    }).catch(() => caches.match(req).then(hit => hit || caches.match("/offline"))));
  }
});
self.addEventListener("push", e => {
  let d = {};
  try { d = e.data ? e.data.json() : {}; } catch (err) { d = {body: e.data && e.data.text()}; }
  e.waitUntil(self.registration.showNotification(d.title || "WorldScope", {
    body: d.body || "", icon: "/static/icon-192.png", badge: "/static/icon-192.png", data: {url: d.url || "/"}, lang: "uz",
  }));
});
self.addEventListener("notificationclick", e => {
  e.notification.close();
  const url = new URL((e.notification.data && e.notification.data.url) || "/", location.origin).href;
  e.waitUntil(clients.matchAll({type: "window", includeUncontrolled: true}).then(list => {
    for (const c of list) if (c.url === url && "focus" in c) return c.focus();
    return clients.openWindow(url);
  }));
});
