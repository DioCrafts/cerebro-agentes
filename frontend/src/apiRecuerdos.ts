// Client for the conversation memory (asistente/plataforma/api/routers/recuerdos.py), kept by Hindsight: «lo que sé
// de ti» — every remembered fact with the conversation and the date it came from — the earlier conversations with
// the user's own messages (the assistant's answers are never kept), and the on/off switch.
//
// Any of these calls can answer 503 while the memory engine starts or after it stops; the server's `detail` says so
// and api.ts's `request` turns it into the ApiError message the panel shows.
//
// Everything goes through api.ts's `request`: same session token, timeout and error messages.

import { request } from "./api";
import { isRecord } from "./lib/guards";
import type { Peticion, Respuesta } from "./api/tipos";

// The shapes come from the backend's OpenAPI schema (src/api/esquema.d.ts).
export type RecuerdosResponse = Respuesta<"/recuerdos", "get">;
// `titulo_conversacion`: the conversation's title while it exists (null once it has been deleted).
export type Recuerdo = RecuerdosResponse["recuerdos"][number];
export type TipoRecuerdo = Recuerdo["tipo"];
export type Interruptor = Respuesta<"/recuerdos/interruptor", "get">;
export type ConversacionesResponse = Respuesta<"/conversaciones", "get">;
export type ConversacionResumen = ConversacionesResponse["conversaciones"][number];
export type ConversacionDetalle = Respuesta<"/conversaciones/{conversacion_id}", "get">;
export type OlvidoTotal = Respuesta<"/recuerdos", "delete">;
export type Correccion = Peticion<"/recuerdos/{recuerdo_id}", "patch">;
export type CorreccionHecha = Respuesta<"/recuerdos/{recuerdo_id}", "patch">;

export const TIPO_RECUERDO_LABEL: Record<TipoRecuerdo, string> = {
  hecho: "Lo que me has contado",
  vivencia: "Lo que te ha pasado",
};

export const TIPOS_RECUERDO: TipoRecuerdo[] = ["hecho", "vivencia"];

// A correction waits while a model reads it and extracts the facts: give it more than the default 30 s.
const CORRECCION_TIMEOUT_MS = 60_000;

function isRecuerdo(value: unknown): value is Recuerdo {
  return (
    isRecord(value) &&
    typeof value.id === "string" &&
    typeof value.texto === "string" &&
    (value.tipo === "hecho" || value.tipo === "vivencia")
  );
}

function isRecuerdos(value: unknown): value is RecuerdosResponse {
  return (
    isRecord(value) &&
    typeof value.activa === "boolean" &&
    typeof value.disponible === "boolean" &&
    Array.isArray(value.recuerdos) &&
    value.recuerdos.every(isRecuerdo) &&
    typeof value.explicacion === "string"
  );
}

function isCorreccion(value: unknown): value is CorreccionHecha {
  return isRecord(value) && Array.isArray(value.recuerdos) && value.recuerdos.every(isRecuerdo);
}

function isInterruptor(value: unknown): value is Interruptor {
  return isRecord(value) && typeof value.activa === "boolean" && typeof value.disponible === "boolean";
}

function isConversaciones(value: unknown): value is ConversacionesResponse {
  return isRecord(value) && Array.isArray(value.conversaciones);
}

function isConversacion(value: unknown): value is ConversacionDetalle {
  return isRecord(value) && isRecord(value.conversacion) && Array.isArray(value.mensajes) && Array.isArray(value.recuerdos);
}

function isOlvido(value: unknown): value is OlvidoTotal {
  return isRecord(value) && typeof value.recuerdos === "number" && typeof value.conversaciones === "number";
}

function isNull(value: unknown): value is null {
  return value === null;
}

/** `?case_id=…` when there is a case; without one the server uses the signed-in account. */
function conCaso(path: string, caseId: string | null): string {
  return caseId ? `${path}?case_id=${encodeURIComponent(caseId)}` : path;
}

export function getRecuerdos(caseId: string | null, signal?: AbortSignal): Promise<RecuerdosResponse> {
  return request(conCaso("/recuerdos", caseId), undefined, isRecuerdos, { signal, method: "GET" });
}

/** The user's wording is kept as their own message and the old fact is invalidated; answers what was learnt from it. */
export function corregirRecuerdo(caseId: string | null, id: string, correccion: Correccion): Promise<CorreccionHecha> {
  return request(conCaso(`/recuerdos/${encodeURIComponent(id)}`, caseId), correccion, isCorreccion, {
    method: "PATCH",
    timeoutMs: CORRECCION_TIMEOUT_MS,
  });
}

/** Deletes the message the item came from, with everything learnt from that message. */
export function olvidarRecuerdo(caseId: string | null, id: string): Promise<null> {
  return request(conCaso(`/recuerdos/${encodeURIComponent(id)}`, caseId), undefined, isNull, { method: "DELETE" });
}

export function olvidarTodo(caseId: string | null): Promise<OlvidoTotal> {
  return request(conCaso("/recuerdos", caseId), undefined, isOlvido, { method: "DELETE" });
}

export function cambiarInterruptor(caseId: string | null, activa: boolean): Promise<Interruptor> {
  return request(conCaso("/recuerdos/interruptor", caseId), { activa }, isInterruptor, { method: "PUT" });
}

export function getConversaciones(caseId: string | null, signal?: AbortSignal): Promise<ConversacionesResponse> {
  return request(conCaso("/conversaciones", caseId), undefined, isConversaciones, { signal, method: "GET" });
}

/** One conversation with the user's kept messages and what was learnt from them. */
export function getConversacion(caseId: string | null, id: string, signal?: AbortSignal): Promise<ConversacionDetalle> {
  return request(conCaso(`/conversaciones/${encodeURIComponent(id)}`, caseId), undefined, isConversacion, {
    signal,
    method: "GET",
  });
}

export function borrarConversacion(caseId: string | null, id: string): Promise<null> {
  return request(conCaso(`/conversaciones/${encodeURIComponent(id)}`, caseId), undefined, isNull, {
    method: "DELETE",
  });
}
