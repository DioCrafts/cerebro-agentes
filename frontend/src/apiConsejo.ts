// Client for the household council (asistente/plataforma/api/routers/consejo.py): hard decisions studied by several
// AIs that work on their own, critique each other anonymously and rule — always after the user confirms the cost.
//
// Everything goes through api.ts's `request`: same session token, timeout and error messages.

import { request } from "./api";
import { isRecord } from "./lib/guards";
import type { Esquemas, Respuesta } from "./api/tipos";

// The shapes come from the backend's OpenAPI schema (src/api/esquema.d.ts).
export type SesionConsejo = Respuesta<"/cases/{case_id}/consejo/{sesion_id}", "get">;
export type ListaSesiones = Respuesta<"/cases/{case_id}/consejo", "get">;
export type ResumenSesion = ListaSesiones["sesiones"][number];
export type Deteccion = Respuesta<"/cases/{case_id}/consejo/detectar", "post">;
export type EstadoSesion = SesionConsejo["estado"];
export type PasoProgreso = Esquemas["PasoProgreso"];
export type CosteSesion = Esquemas["CosteSesion"];
export type ExpedienteConsejo = Esquemas["ExpedienteConsejo"];
export type CifraConsejo = ExpedienteConsejo["cifras"][number];
export type OpcionConsejo = ExpedienteConsejo["opciones"][number];
export type HechoExpediente = ExpedienteConsejo["hechos"][number];
export type PropuestaConsejo = SesionConsejo["propuestas"][number];
export type CriticaConsejo = Esquemas["Critica"];
export type RankingConsejo = Esquemas["Ranking"];
export type Dictamen = Esquemas["Dictamen"];
export type Consenso = Dictamen["consenso"];
export type MiembroConsejo = Dictamen["miembros"][number];
export type RolConsejo = MiembroConsejo["rol"];
export type PosturaOpcion = Dictamen["posturas"][number];
export type AtribucionCifraConsejo = NonNullable<Dictamen["puerta"]>["atribuciones"][number];

/** States in which the council is still working (the UI polls them). */
export const EN_MARCHA: ReadonlySet<EstadoSesion> = new Set(["en_cola", "instruyendo", "deliberando", "sintetizando"]);

export const ESTADO_LABEL: Record<EstadoSesion, string> = {
  pendiente_confirmacion: "Esperando tu confirmación",
  en_cola: "En cola",
  instruyendo: "Preparando el expediente",
  deliberando: "Deliberando",
  sintetizando: "Redactando el dictamen",
  terminada: "Dictamen listo",
  fallida: "No pudo terminar",
  cancelada: "Cancelada",
  rechazada: "Descartada",
};

export function isSesion(value: unknown): value is SesionConsejo {
  return isRecord(value) && typeof value.id === "string" && typeof value.estado === "string" && Array.isArray(value.progreso);
}

function isLista(value: unknown): value is ListaSesiones {
  return isRecord(value) && typeof value.disponible === "boolean" && Array.isArray(value.sesiones);
}

function isDeteccion(value: unknown): value is Deteccion {
  return isRecord(value) && typeof value.dificil === "boolean" && Array.isArray(value.motivos);
}

function base(caseId: string): string {
  return `/cases/${encodeURIComponent(caseId)}/consejo`;
}

export function listarSesiones(caseId: string, signal?: AbortSignal): Promise<ListaSesiones> {
  return request(base(caseId), undefined, isLista, { signal, method: "GET" });
}

/** The session's live stream (Server-Sent Events `sesion`, then `fin`): see shell/useSesionConsejo.ts. */
export function rutaEventosSesion(caseId: string, sesionId: string): string {
  return `${base(caseId)}/${encodeURIComponent(sesionId)}/eventos`;
}

export function obtenerSesion(caseId: string, sesionId: string, signal?: AbortSignal): Promise<SesionConsejo> {
  return request(`${base(caseId)}/${encodeURIComponent(sesionId)}`, undefined, isSesion, { signal, method: "GET" });
}

export function convocarConsejo(
  caseId: string,
  datos: { pregunta: string; opciones?: string[]; preferencia?: string | null },
): Promise<SesionConsejo> {
  return request(base(caseId), datos, isSesion);
}

export function detectarDecision(caseId: string, pregunta: string, signal?: AbortSignal): Promise<Deteccion> {
  return request(`${base(caseId)}/detectar`, { pregunta }, isDeteccion, { signal });
}

function accion(caseId: string, sesionId: string, que: "confirmar" | "rechazar" | "cancelar"): Promise<SesionConsejo> {
  return request(`${base(caseId)}/${encodeURIComponent(sesionId)}/${que}`, {}, isSesion);
}

export const confirmarSesion = (caseId: string, sesionId: string) => accion(caseId, sesionId, "confirmar");
export const rechazarSesion = (caseId: string, sesionId: string) => accion(caseId, sesionId, "rechazar");
export const cancelarSesion = (caseId: string, sesionId: string) => accion(caseId, sesionId, "cancelar");

export function borrarSesion(caseId: string, sesionId: string): Promise<null> {
  return request(`${base(caseId)}/${encodeURIComponent(sesionId)}`, undefined, (v): v is null => v === null, {
    method: "DELETE",
  });
}

/** The engine's partial-ruling warning starts with this. */
export const PREFIJO_PARCIAL = "Dictamen parcial:";

/** «0,40 USD». */
export function usd(valor: string | number): string {
  const n = typeof valor === "number" ? valor : Number(valor);
  return Number.isFinite(n) ? `${n.toLocaleString("es-ES", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} USD` : String(valor);
}

/** The council's confidence in plain words. */
export function confianzaEnPalabras(confianza: number): string {
  if (confianza >= 0.75) return "Confianza alta";
  if (confianza >= 0.5) return "Confianza media";
  if (confianza >= 0.3) return "Confianza baja";
  return "Confianza muy baja";
}

export const CONSENSO_LABEL: Record<Consenso, string> = {
  unanime: "Unanimidad",
  mayoria: "Mayoría",
  dividido: "Consejo dividido",
  insuficiente: "Sin base suficiente",
};
