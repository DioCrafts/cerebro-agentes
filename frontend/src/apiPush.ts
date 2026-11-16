// Client for Web Push (hacienda_gpt/api/routers/push.py): the opt-in, this device's subscription, a test.
// Everything goes through api.ts's `request`: same session token, timeout and error messages.

import { request } from "./api";

export interface PushConfig {
  disponible: boolean;
  clave_publica: string | null;
  consentimiento: boolean;
  dispositivos: number;
  version_texto: string;
}

export interface EstadoPush {
  consentimiento: boolean;
  dispositivos: number;
}

export interface PruebaPush {
  enviadas: number;
  errores: number;
  caducadas: number;
}

function esObjeto(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null;
}

function esConfig(v: unknown): v is PushConfig {
  return esObjeto(v) && typeof v.disponible === "boolean" && typeof v.consentimiento === "boolean" && typeof v.dispositivos === "number";
}

function esEstado(v: unknown): v is EstadoPush {
  return esObjeto(v) && typeof v.consentimiento === "boolean" && typeof v.dispositivos === "number";
}

function esPrueba(v: unknown): v is PruebaPush {
  return esObjeto(v) && typeof v.enviadas === "number" && typeof v.errores === "number";
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
