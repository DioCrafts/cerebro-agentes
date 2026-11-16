// Client for the user's remembered decisions (hacienda_gpt/api/routers/memoria.py): what they rejected,
// accepted, prefer or cannot do, so the assistant never proposes again what they said no to.
//
// Everything goes through api.ts's `request`: same session token, timeout and error messages.

import { request } from "./api";

export type TipoDecision = "rechazo" | "aceptacion" | "preferencia" | "restriccion";
export type EstadoDecision = "vigente" | "reabrible" | "caducada" | "reabierta" | "sustituida";
export type BotonAviso = "no_me_interesa" | "ya_lo_hice" | "recuerdamelo";

export interface Condicion {
  tipo: "fecha" | "hecho" | "mejora";
  fecha?: string | null;
  hecho?: string | null;
  factor?: string | number | null;
  importe_referencia?: string | number | null;
  descripcion?: string;
}

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

export interface DecisionesResponse {
  case_id: string;
  hoy: string;
  decisiones: Decision[];
  catalogo: Array<{ ambito: string; etiqueta: string }>;
  historial: EventoHistorial[];
}

export interface DecisionResponse {
  decision: Decision;
  avisos_retirados: number;
  plan_recalculado: boolean;
  resumen_recalculado: boolean;
}

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

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

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

export interface NuevaDecision {
  ambito?: string;
  palanca?: string;
  opcion?: string | null;
  tipo?: TipoDecision;
  razon?: string;
  caducidad?: string | null;
  condiciones_de_reapertura?: Condicion[];
  propuesta?: { titulo?: string; importe?: string | null };
  boton?: "no_me_interesa" | "ya_lo_hice";
}

export function crearDecision(caseId: string, body: NuevaDecision): Promise<DecisionResponse> {
  return request(base(caseId), body, isDecisionResponse);
}

export function editarDecision(
  caseId: string,
  id: string,
  cambios: { tipo?: TipoDecision; razon?: string; caducidad?: string | null },
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
  body: { boton: BotonAviso; razon?: string; hasta?: string },
): Promise<DecisionResponse> {
  return request(`${base(caseId)}/aviso/${encodeURIComponent(notificationId)}`, body, isDecisionResponse);
}
