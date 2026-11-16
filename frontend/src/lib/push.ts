// Web Push on this device: ask the browser for permission, subscribe with the server's VAPID key, and
// keep the server's record in step. The server only ever sends a generic sentence («Tienes un aviso…»).

import { bajaPush, getPushConfig, retirarConsentimientoPush, suscribirPush } from "../apiPush";
import { admitePush } from "./pwa";

function claveAplicacion(base64url: string): Uint8Array<ArrayBuffer> {
  const relleno = "=".repeat((4 - (base64url.length % 4)) % 4);
  const binario = atob((base64url + relleno).replace(/-/g, "+").replace(/_/g, "/"));
  const salida = new Uint8Array(new ArrayBuffer(binario.length));
  for (let i = 0; i < binario.length; i++) salida[i] = binario.charCodeAt(i);
  return salida;
}

function b64url(buffer: ArrayBuffer | null): string {
  if (!buffer) return "";
  let binario = "";
  for (const byte of new Uint8Array(buffer)) binario += String.fromCharCode(byte);
  return btoa(binario).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

async function registro(): Promise<ServiceWorkerRegistration | null> {
  if (!admitePush()) return null;
  return (await navigator.serviceWorker.getRegistration()) ?? null;
}

/** This browser's current subscription, if any. */
export async function suscripcionActual(): Promise<PushSubscription | null> {
  const reg = await registro();
  return reg ? reg.pushManager.getSubscription() : null;
}

export class PushNoDisponibleError extends Error {}

/** Opt in on this device: permission, subscription, and the server's record (with the user's consent). */
export async function activarPush(clavePublica: string): Promise<void> {
  const reg = await registro();
  if (!reg) throw new PushNoDisponibleError("Este navegador no puede recibir avisos aquí.");
  const permiso = await Notification.requestPermission();
  if (permiso !== "granted") throw new PushNoDisponibleError("No has dado permiso para mostrar notificaciones.");
  let sub = await reg.pushManager.getSubscription();
  const clave = claveAplicacion(clavePublica);
  // A subscription made with another server key (the operator rotated it) cannot be used.
  if (sub && b64url(sub.options.applicationServerKey) !== b64url(clave.buffer)) {
    await sub.unsubscribe();
    sub = null;
  }
  sub ??= await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: clave });
  await suscribirPush({ endpoint: sub.endpoint, p256dh: b64url(sub.getKey("p256dh")), auth: b64url(sub.getKey("auth")) });
}

/** Stop on this device (the browser forgets the subscription and the server too). */
export async function desactivarPush(): Promise<void> {
  const sub = await suscripcionActual();
  if (!sub) return;
  await bajaPush(sub.endpoint).catch(() => undefined);
  await sub.unsubscribe().catch(() => false);
}

/** Withdraw the consent: every device of the user is forgotten by the server, this one by the browser. */
export async function retirarPush(): Promise<void> {
  await retirarConsentimientoPush();
  const sub = await suscripcionActual();
  await sub?.unsubscribe().catch(() => false);
}

/** Before signing out: this device must not keep receiving the account's notices. Best effort. */
export async function olvidarDispositivo(): Promise<void> {
  try {
    await desactivarPush();
  } catch {
    // Signing out must never fail because of this.
  }
}

/**
 * At start-up: if the user opted in and this browser still has a permission and a subscription, make sure the
 * server knows it (the browser may have renewed it). Never subscribes on its own.
 */
export async function sincronizarPush(): Promise<void> {
  try {
    if (!admitePush() || Notification.permission !== "granted") return;
    const sub = await suscripcionActual();
    if (!sub) return;
    const config = await getPushConfig();
    if (!config.disponible) return;
    if (!config.consentimiento) {
      // Withdrawn elsewhere, or another account signed in on this browser: this device stops receiving.
      await sub.unsubscribe().catch(() => false);
      return;
    }
    await suscribirPush({ endpoint: sub.endpoint, p256dh: b64url(sub.getKey("p256dh")), auth: b64url(sub.getKey("auth")) });
  } catch {
    // Best effort.
  }
}
