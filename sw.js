// Service worker: la app abre aunque no haya cobertura, con lo último que se cargó.
// Siempre intenta primero la red, así los datos y los cambios de la página llegan al momento.
const CACHE = "cdca-v2";
const BASE = ["./", "index.html", "manifest.json", "icon-192.png", "icon-512.png", "apple-touch-icon.png"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(BASE)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys()
      .then((claves) => Promise.all(claves.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (e) => {
  const req = e.request;
  const url = new URL(req.url);
  if (req.method !== "GET" || url.origin !== location.origin) return;

  // La clave de caché ignora "?t=..." para que partidos.json se guarde siempre en el mismo sitio
  const clave = new Request(url.origin + url.pathname);
  e.respondWith(
    fetch(req)
      .then((resp) => {
        if (resp.ok) {
          const copia = resp.clone();
          caches.open(CACHE).then((c) => c.put(clave, copia));
        }
        return resp;
      })
      .catch(() => caches.match(clave).then((r) => r || caches.match("index.html")))
  );
});
