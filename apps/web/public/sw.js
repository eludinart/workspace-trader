const CACHE = "workspace-trader-static-v4";
const ASSETS = ["/manifest.webmanifest", "/icon.svg"];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(ASSETS)));
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))
    )
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  const { request } = event;
  if (request.method !== "GET") return;

  // Never cache HTML / navigations / Next chunks — avoid stale DOM vs React
  const url = new URL(request.url);
  if (
    request.mode === "navigate" ||
    request.destination === "document" ||
    url.pathname.startsWith("/api/") ||
    url.pathname.startsWith("/_next/")
  ) {
    return;
  }

  if (ASSETS.some((a) => url.pathname === a)) {
    event.respondWith(
      caches.match(request).then((cached) => cached || fetch(request))
    );
  }
});

/** Web Push foundations (point 19) — payload JSON { title, body, url }. */
self.addEventListener("push", (event) => {
  let title = "workspace-trader";
  let body = "Alerte paper";
  let url = "/";
  try {
    if (event.data) {
      const data = event.data.json();
      title = data.title || title;
      body = data.body || body;
      url = data.url || url;
    }
  } catch (_) {
    try {
      body = event.data ? event.data.text() : body;
    } catch (__) {
      /* ignore */
    }
  }
  event.waitUntil(
    self.registration.showNotification(title, {
      body,
      data: { url },
      icon: "/icon.svg",
    })
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const target = (event.notification.data && event.notification.data.url) || "/";
  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((clients) => {
      for (const client of clients) {
        if ("focus" in client) {
          client.navigate(target);
          return client.focus();
        }
      }
      if (self.clients.openWindow) return self.clients.openWindow(target);
      return undefined;
    })
  );
});
