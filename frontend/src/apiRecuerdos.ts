// Client for the conversation memory (asistente/plataforma/api/routers/recuerdos.py): «lo que sé de ti» — every
// remembered item with where it came from (the conversation, the date and the user's own words) — the earlier
// conversations with their summaries, and the on/off switch.
//
// Everything goes through api.ts's `request`: same session token, timeout and error messages.

import { request } from "./api";
import { isRecord } from "./lib/guards";

export type TipoRecuerdo = "dato" | "preferencia" | "contexto";

export interface FuenteRecuerdo {
  conversacion_id: string;
  fecha: string;
  cita: string;
  /** The conversation's title while it exists (null: deleted, or its messages were purged before a summary). */
  titulo_conversacion: string | null;
}

export interface Recuerdo {
  id: string;
  tipo: TipoRecuerdo;
  texto: string;
  origen: FuenteRecuerdo;
  fuentes: FuenteRecuerdo[];
  corregido: boolean;
  sensible: boolean;
  estado: "activo" | "sustituido";
  sustituido_por: string | null;
  creado: string;
  actualizado: string;
  usos: number;
  ultimo_uso: string | null;
}

export interface RecuerdosResponse {
  disponible: boolean;
  activa: boolean;
  sin_cuenta: boolean;
  retencion_dias: number;
  explicacion: string;
  recuerdos: Recuerdo[];
  historial: Recuerdo[];
  conversaciones: number;
}

export interface Interruptor {
  disponible: boolean;
  activa: boolean;
  explicacion: string;
}

export interface ConversacionResumen {
  id: string;
  titulo: string;
  creada: string;
  actualizada: string;
  estado: string;
  resumen: string | null;
  cita: string | null;
  mensajes: number;
  retenidos: number;
  recuerdos: number;
}

export interface ConversacionesResponse {
  retencion_dias: number;
  conversaciones: ConversacionResumen[];
}

export interface OlvidoTotal {
  recuerdos: number;
  conversaciones: number;
  mensajes: number;
}

export const TIPO_RECUERDO_LABEL: Record<TipoRecuerdo, string> = {
  preferencia: "Cómo prefieres las cosas",
  dato: "Datos que me has contado",
  contexto: "Tu situación ahora",
};

export const TIPOS_RECUERDO: TipoRecuerdo[] = ["preferencia", "dato", "contexto"];

function isRecuerdo(value: unknown): value is Recuerdo {
  return isRecord(value) && typeof value.id === "string" && typeof value.texto === "string" && isRecord(value.origen);
}

function isRecuerdos(value: unknown): value is RecuerdosResponse {
  return (
    isRecord(value) &&
    typeof value.activa === "boolean" &&
    Array.isArray(value.recuerdos) &&
    Array.isArray(value.historial) &&
    typeof value.explicacion === "string"
  );
}

function isInterruptor(value: unknown): value is Interruptor {
  return isRecord(value) && typeof value.activa === "boolean" && typeof value.disponible === "boolean";
}

function isConversaciones(value: unknown): value is ConversacionesResponse {
  return isRecord(value) && Array.isArray(value.conversaciones);
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

export function corregirRecuerdo(
  caseId: string | null,
  id: string,
  cambios: { texto: string; tipo?: TipoRecuerdo },
): Promise<Recuerdo> {
  return request(conCaso(`/recuerdos/${encodeURIComponent(id)}`, caseId), cambios, isRecuerdo, { method: "PATCH" });
}

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

export function borrarConversacion(caseId: string | null, id: string): Promise<null> {
  return request(conCaso(`/conversaciones/${encodeURIComponent(id)}`, caseId), undefined, isNull, {
    method: "DELETE",
  });
}
