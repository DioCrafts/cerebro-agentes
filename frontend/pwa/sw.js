// Service worker of HaciendaGPT (template: vite.config.ts fills in VERSION and PRECACHE at build time and
// writes it as dist/sw.js).
//
// PRIVACY RULE — what this worker may store: ONLY the app's own static files listed in PRECACHE (the HTML
// shell, the JS/CSS bundles, the icons and the manifest), taken from the build output at build time. It never
// calls cache.put() on anything fetched at run time, and it never answers a request that is not in that list
// or a navigation to the app's start page. Every other request — /api/…, /cases, /me, /auth, /push, the
// OAuth callbacks, anything else — is not intercepted at all: the browser sends it to the network as if
// there were no service worker ("network only"), and the server marks API responses `Cache-Control:
// no-store`. Personal data therefore never lands in the Cache Storage.
//
// Offline: the shell opens from the cache and shows that the service is unreachable; nothing personal is
// available offline (conversations stay in the page's own localStorage, as before).
//
// Updates: a new build has a new VERSION, so the browser installs the new worker next to the old one; it
// waits until the page asks it to take over («Hay una versión nueva» → «Actualizar»).
//
// Push: shows the generic sentence the server sent (never personal data) and opens the app on click.

const VERSION = "__VERSION__";
const PRECACHE = __PRECACHE__;
const CACHE = `haciendagpt-shell-${VERSION}`;
const PREFIJO = "haciendagpt-shell-";
const SHELL = "index.html";

const BASE = new URL(self.registration.scope);
const EN_CACHE = new Set(PRECACHE.map((ruta) => new URL(ruta, BASE).href));
const INICIO = new URL(SHELL, BASE).href;

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE).then((cache) =>
      // `reload`: straight from the network, not from the HTTP cache of an older build.
      cache.addAll(PRECACHE.map((ruta) => new Request(new URL(ruta, BASE).href, { cache: "reload" }))),
    ),
  );
  // The very first install takes over at once; an update waits for «Actualizar».
  if (!self.registration.active) self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    (async () => {
      for (const nombre of await caches.keys()) {
        if (nombre.startsWith(PREFIJO) && nombre !== CACHE) await caches.delete(nombre);
      }
      await self.clients.claim();
    })(),
  );
});

self.addEventListener("message", (event) => {
  if (event.data && event.data.tipo === "ACTUALIZAR") self.skipWaiting();
});

/** The app's start page: the scope itself or index.html, with any query (`?panel=…`). */
function esInicio(url) {
  return url.origin === BASE.origin && (url.pathname === BASE.pathname || url.href.split("?")[0] === INICIO);
}

self.addEventListener("fetch", (event) => {
  const peticion = event.request;
  if (peticion.method !== "GET") return;
  const url = new URL(peticion.url);
  if (url.origin !== BASE.origin) return;
  if (peticion.mode === "navigate") {
    if (!esInicio(url)) return; // e.g. /api/correo/callback: straight to the network
    event.respondWith(
      caches.match(INICIO, { cacheName: CACHE }).then((guardada) => guardada || fetch(peticion)),
    );
    return;
  }
  const clave = url.origin + url.pathname;
  if (!EN_CACHE.has(clave) || url.search) return; // not one of the app's own files: network only
  event.respondWith(caches.match(clave, { cacheName: CACHE }).then((guardada) => guardada || fetch(peticion)));
});

function mismoOrigen(ruta) {
  try {
    const url = new URL(ruta, BASE);
    return url.origin === BASE.origin ? url.href : null;
  } catch (e) {
    return null;
  }
}

self.addEventListener("push", (event) => {
  let datos = {};
  try {
    datos = event.data ? event.data.json() : {};
  } catch (e) {
    datos = {};
  }
  const titulo = typeof datos.titulo === "string" ? datos.titulo.slice(0, 80) : "HaciendaGPT";
  const cuerpo = typeof datos.cuerpo === "string" ? datos.cuerpo.slice(0, 160) : "Tienes un aviso nuevo: abre la app";
  const destino = (typeof datos.url === "string" && mismoOrigen(datos.url)) || new URL("?panel=avisos", BASE).href;
  event.waitUntil(
    self.registration.showNotification(titulo, {
      body: cuerpo,
      icon: new URL("icons/icono-192.png", BASE).href,
      badge: new URL("icons/icono-192.png", BASE).href,
      tag: typeof datos.etiqueta === "string" ? datos.etiqueta : "haciendagpt-avisos",
      renotify: true,
      lang: "es",
      data: { url: destino },
    }),
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const destino = (event.notification.data && mismoOrigen(event.notification.data.url)) || BASE.href;
  event.waitUntil(
    (async () => {
      const ventanas = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
      for (const ventana of ventanas) {
        if (new URL(ventana.url).origin === BASE.origin && "focus" in ventana) {
          await ventana.focus();
          if ("navigate" in ventana) await ventana.navigate(destino).catch(() => undefined);
          return;
        }
      }
      await self.clients.openWindow(destino);
    })(),
  );
});
