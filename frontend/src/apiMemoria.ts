// Client for the user's remembered decisions (asistente/plataforma/api/routers/memoria.py): what they rejected,
// accepted, prefer or cannot do, so the assistant never proposes again what they said no to.
//
// Everything goes through api.ts's `request`: same session token, timeout and error messages.

import { request } from "./api";
import { isRecord } from "./lib/guards";
import type { Esquemas, Peticion, Respuesta } from "./api/tipos";

// The bodies come from the backend's OpenAPI schema (src/api/esquema.d.ts). The decision itself and its history
// travel as plain dicts (`dict[str, Any]` in the schema), so Decision and EventoHistorial are typed here by hand,
// after asistente/nucleo/memoria (Decision.to_dict).
export type NuevaDecision = Peticion<"/cases/{case_id}/decisiones", "post">;
export type CambiosDecision = Peticion<"/cases/{case_id}/decisiones/{decision_id}", "put">;
export type RespuestaAviso = Peticion<"/cases/{case_id}/decisiones/aviso/{notification_id}", "post">;
export type TipoDecision = NonNullable<NuevaDecision["tipo"]>;
export type BotonAviso = RespuestaAviso["boton"];
export type Condicion = Esquemas["Condicion"];
export type EstadoDecision = "vigente" | "reabrible" | "caducada" | "reabierta" | "sustituida";

export interface Decision {
  id: string;
  case_id: string;
  ambito: string;
  etiqueta: string;
  modulo: string;
  tipo: TipoDecision;
  razon: string;
  fuente: "chat" | "boton" | "ajustes" | "accion";
  boton?: BotonAviso | null;
  fecha: string;
  caducidad?: string | null;
  condiciones_de_reapertura: Condicion[];
  propuesta?: { titulo?: string; importe?: string | null; clave_aviso?: string | null } | null;
  reabierta_el?: string | null;
  reabierta_motivo?: string | null;
  sustituida_por?: string | null;
  omisiones: number;
  ultima_omision?: string | null;
  estado: EstadoDecision;
  motivo_estado?: string | null;
}

export interface EventoHistorial {
  decision_id: string;
  momento: string;
  evento: string;
  ambito: string;
  tipo: TipoDecision;
  razon: string;
}

export type DecisionesResponse = Omit<Respuesta<"/cases/{case_id}/decisiones", "get">, "decisiones" | "historial"> & {
  decisiones: Decision[];
  historial: EventoHistorial[];
};

export type DecisionResponse = Omit<Respuesta<"/cases/{case_id}/decisiones", "post">, "decision"> & { decision: Decision };

export const TIPO_LABEL: Record<TipoDecision, string> = {
  rechazo: "No me interesa",
  restriccion: "No puedo",
  preferencia: "Prefiero",
  aceptacion: "Ya lo hice",
};

export const ESTADO_LABEL: Record<EstadoDecision, string> = {
  vigente: "En vigor",
  reabrible: "Se puede volver a proponer",
  caducada: "Caducada",
  reabierta: "Ya se volvió a proponer",
  sustituida: "Sustituida por otra",
};

function isDecisiones(value: unknown): value is DecisionesResponse {
  return isRecord(value) && Array.isArray(value.decisiones) && Array.isArray(value.catalogo);
}

function isDecisionResponse(value: unknown): value is DecisionResponse {
  return isRecord(value) && isRecord(value.decision) && typeof value.avisos_retirados === "number";
}

function isNull(value: unknown): value is null {
  return value === null;
}

const base = (caseId: string) => `/cases/${encodeURIComponent(caseId)}/decisiones`;

export function getDecisiones(caseId: string, signal?: AbortSignal): Promise<DecisionesResponse> {
  return request(base(caseId), undefined, isDecisiones, { signal, method: "GET" });
}

export function crearDecision(caseId: string, body: NuevaDecision): Promise<DecisionResponse> {
  return request(base(caseId), body, isDecisionResponse);
}

export function editarDecision(
  caseId: string,
  id: string,
  cambios: CambiosDecision,
): Promise<DecisionResponse> {
  return request(`${base(caseId)}/${encodeURIComponent(id)}`, cambios, isDecisionResponse, { method: "PUT" });
}

export function borrarDecision(caseId: string, id: string): Promise<null> {
  return request(`${base(caseId)}/${encodeURIComponent(id)}`, undefined, isNull, { method: "DELETE" });
}

/** A button on a notice: the notice leaves the inbox and the answer is remembered. */
export function responderAviso(
  caseId: string,
  notificationId: string,
  body: RespuestaAviso,
): Promise<DecisionResponse> {
  return request(`${base(caseId)}/aviso/${encodeURIComponent(notificationId)}`, body, isDecisionResponse);
}
