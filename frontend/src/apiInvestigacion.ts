// Client for «Investigar» (asistente/plataforma/api/routers/investigacion.py): research with sources.
//
// The shapes come from the backend's OpenAPI schema (src/api/esquema.d.ts). Money comes
// as decimal strings. Everything goes through api.ts's `request`: same session token, timeout and errors.

import { request } from "./api";
import { isRecord } from "./lib/guards";
import type { Peticion, Respuesta } from "./api/tipos";

export type Informe = Respuesta<"/investigacion", "post">;
export type PeticionInvestigacion = Peticion<"/investigacion", "post">;
export type EstadoInvestigacion = Respuesta<"/investigacion/estado", "get">;
export type Afirmacion = Informe["afirmaciones"][number];
export type Cita = Afirmacion["citas"][number];
export type FuenteInforme = Informe["fuentes"][number];
export type Conflicto = Informe["conflictos"][number];
export type TipoFuente = FuenteInforme["tipo"];
export type Profundidad = Informe["profundidad"];
export type Confianza = Informe["confianza"];

function esEstado(v: unknown): v is EstadoInvestigacion {
  return isRecord(v) && isRecord(v.fuentes) && typeof v.web_consentida === "boolean" && typeof v.privacidad === "string";
}

function esInforme(v: unknown): v is Informe {
  return (
    isRecord(v) &&
    typeof v.respuesta === "string" &&
    Array.isArray(v.afirmaciones) &&
    Array.isArray(v.fuentes) &&
    isRecord(v.coste) &&
    isRecord(v.web)
  );
}

// A research makes several model calls and may read web pages: give it time.
const TIEMPO_INVESTIGACION_MS = 180_000;

export function getEstadoInvestigacion(signal?: AbortSignal): Promise<EstadoInvestigacion> {
  return request("/investigacion/estado", undefined, esEstado, { method: "GET", signal });
}

export function cambiarConsentimientoWeb(concedido: boolean): Promise<EstadoInvestigacion> {
  return request("/investigacion/consentimiento", { concedido }, esEstado);
}

export function investigar(peticion: PeticionInvestigacion, signal?: AbortSignal): Promise<Informe> {
  return request("/investigacion", peticion, esInforme, { signal, timeoutMs: TIEMPO_INVESTIGACION_MS });
}
