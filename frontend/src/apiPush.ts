// Client for Web Push (asistente/plataforma/api/routers/push.py): the opt-in, this device's subscription, a test.
// Everything goes through api.ts's `request`: same session token, timeout and error messages.

import { request } from "./api";
import { isRecord } from "./lib/guards";
import type { Respuesta } from "./api/tipos";

// The shapes come from the backend's OpenAPI schema (src/api/esquema.d.ts).
export type PushConfig = Respuesta<"/push/config", "get">;
export type EstadoPush = Respuesta<"/push/suscripciones/baja", "post">;
export type PruebaPush = Respuesta<"/push/prueba", "post">;

function esConfig(v: unknown): v is PushConfig {
  return isRecord(v) && typeof v.disponible === "boolean" && typeof v.consentimiento === "boolean" && typeof v.dispositivos === "number";
}

function esEstado(v: unknown): v is EstadoPush {
  return isRecord(v) && typeof v.consentimiento === "boolean" && typeof v.dispositivos === "number";
}

function esPrueba(v: unknown): v is PruebaPush {
  return isRecord(v) && typeof v.enviadas === "number" && typeof v.errores === "number";
}

export function getPushConfig(signal?: AbortSignal): Promise<PushConfig> {
  return request("/push/config", undefined, esConfig, { signal, method: "GET" });
}

/** Opt in (explicit consent) and register this device's subscription. */
export function suscribirPush(sub: { endpoint: string; p256dh: string; auth: string }): Promise<EstadoPush> {
  return request(
    "/push/suscripciones",
    { endpoint: sub.endpoint, claves: { p256dh: sub.p256dh, auth: sub.auth }, consentimiento: true },
    esEstado,
  );
}

/** Remove this device (the opt-in stays for the others). */
export function bajaPush(endpoint: string): Promise<EstadoPush> {
  return request("/push/suscripciones/baja", { endpoint }, esEstado);
}

/** Withdraw the opt-in: every device of the user is forgotten. */
export function retirarConsentimientoPush(): Promise<EstadoPush> {
  return request("/push/consentimiento", undefined, esEstado, { method: "DELETE" });
}

export function pruebaPush(): Promise<PruebaPush> {
  return request("/push/prueba", {}, esPrueba);
}
