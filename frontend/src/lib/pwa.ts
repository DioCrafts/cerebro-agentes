// The installable app (PWA): the service worker (public shell offline, «Hay una versión nueva»), the
// install prompt, and what the platform allows.
//
// The service worker (pwa/sw.js, built by vite.config.ts) only serves the app's own files from its cache;
// it never stores an API response (see the header of pwa/sw.js). It is registered in production builds only.

import { useSyncExternalStore } from "react";

interface EventoInstalacion extends Event {
  prompt: () => Promise<void>;
  userChoice: Promise<{ outcome: "accepted" | "dismissed" }>;
}

interface EstadoPwa {
  /** A new version is installed and waiting: «Hay una versión nueva». */
  actualizacion: boolean;
  /** The browser offers its own install prompt (Chromium on Android and desktop). */
  instalable: boolean;
  /** The service worker controls this page (needed for push). */
  registro: ServiceWorkerRegistration | null;
}

let estado: EstadoPwa = { actualizacion: false, instalable: false, registro: null };
let eventoInstalacion: EventoInstalacion | null = null;
let recargarAlCambiar = false;
const oyentes = new Set<() => void>();

function cambiar(parcial: Partial<EstadoPwa>) {
  estado = { ...estado, ...parcial };
  for (const oyente of oyentes) oyente();
}

export function usePwa(): EstadoPwa {
  return useSyncExternalStore(
    (oyente) => {
      oyentes.add(oyente);
      return () => oyentes.delete(oyente);
    },
    () => estado,
    () => estado,
  );
}

/** iPhone / iPad (iPadOS reports itself as a Mac with touch). */
export function esIOS(): boolean {
  const ua = navigator.userAgent;
  return /iPad|iPhone|iPod/.test(ua) || (ua.includes("Macintosh") && navigator.maxTouchPoints > 1);
}

/** Opened from the home screen / as an installed app. */
export function enModoApp(): boolean {
  const iosStandalone = (navigator as Navigator & { standalone?: boolean }).standalone === true;
  return iosStandalone || window.matchMedia?.("(display-mode: standalone)").matches === true;
}

/** The browser can receive Web Push here (on iOS only once installed on the home screen, iOS 16.4+). */
export function admitePush(): boolean {
  return window.isSecureContext && "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;
}

export function admiteServiceWorker(): boolean {
  return window.isSecureContext && "serviceWorker" in navigator;
}

function vigilar(registro: ServiceWorkerRegistration) {
  const marcar = () => {
    // Only an UPDATE waits: the first install has no controller to replace.
    if (registro.waiting && navigator.serviceWorker.controller) cambiar({ actualizacion: true });
  };
  marcar();
  registro.addEventListener("updatefound", () => {
    const nuevo = registro.installing;
    nuevo?.addEventListener("statechange", () => {
      if (nuevo.state === "installed") marcar();
    });
  });
}

/** Register the service worker (production builds). Safe to call once at start-up. */
export function iniciarPwa(): void {
  window.addEventListener("beforeinstallprompt", (e) => {
    e.preventDefault();
    eventoInstalacion = e as EventoInstalacion;
    cambiar({ instalable: true });
  });
  window.addEventListener("appinstalled", () => {
    eventoInstalacion = null;
    cambiar({ instalable: false });
  });
  if (!import.meta.env.PROD || !admiteServiceWorker()) return;
  navigator.serviceWorker.addEventListener("controllerchange", () => {
    if (recargarAlCambiar) window.location.reload();
  });
  const base = import.meta.env.BASE_URL;
  navigator.serviceWorker
    .register(`${base}sw.js`, { scope: base, updateViaCache: "none" })
    .then((registro) => {
      cambiar({ registro });
      vigilar(registro);
      // Look for a new version when the app comes back to the foreground, and every hour.
      const buscar = () => void registro.update().catch(() => undefined);
      document.addEventListener("visibilitychange", () => document.visibilityState === "visible" && buscar());
      setInterval(buscar, 60 * 60 * 1000);
    })
    .catch((err: unknown) => console.warn("service worker registration failed", err));
}

/** «Actualizar»: the waiting version takes over and the page reloads with it. */
export function aplicarActualizacion(): void {
  const esperando = estado.registro?.waiting;
  if (!esperando) {
    window.location.reload();
    return;
  }
  recargarAlCambiar = true;
  esperando.postMessage({ tipo: "ACTUALIZAR" });
}

export function descartarActualizacion(): void {
  cambiar({ actualizacion: false });
}

/** Show the browser's install prompt (when it offered one). */
export async function instalar(): Promise<boolean> {
  if (!eventoInstalacion) return false;
  const evento = eventoInstalacion;
  eventoInstalacion = null;
  cambiar({ instalable: false });
  await evento.prompt();
  const { outcome } = await evento.userChoice;
  return outcome === "accepted";
}
