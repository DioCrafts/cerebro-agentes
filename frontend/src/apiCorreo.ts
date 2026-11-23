// Client for the mail module (asistente/plataforma/api/routers/correo.py): Gmail in read-only mode.
//
// The Google consent screen is reached with a full-page navigation to the URL the API returns; Google
// sends the browser back to the API, which stores the (encrypted) permission and redirects here with
// `?correo=conectado|cancelado|error`. No Google credential ever passes through this code.

import { request } from "./api";
import { isRecord } from "./lib/guards";
import type { Esquemas, Respuesta } from "./api/tipos";

// The shapes come from the backend's OpenAPI schema (src/api/esquema.d.ts), except the digest's: GET /correo/resumen
// answers a plain dict (asistente/capacidades/correo/resumen.py -> resumen_dict) with no model, so its types
// (ResumenCorreo, SeccionResumen, ElementoCorreo) are kept here by hand.
// `estado`: "conectada" | "reconectar" | null.
export type EstadoCorreo = Respuesta<"/correo/estado", "get">;
export type CampoCorreo = Esquemas["CampoGuardado"];
export type PropuestaCorreo = Respuesta<"/correo/propuestas", "get">["propuestas"][number];
export type EstadoPropuesta = PropuestaCorreo["estado"];
export type SincronizacionCorreo = Respuesta<"/correo/sincronizar", "post">;
export type DesconexionCorreo = Respuesta<"/correo/desconectar", "post">;

export interface ElementoCorreo {
  id: string;
  fecha: string;
  dominio: string;
  categoria: string;
  etiqueta: string;
  sospechoso: boolean;
  motivos: string[];
  asunto?: string | null;
  campos: CampoCorreo[];
  plazo?: string | null;
  requiere_accion: boolean;
  organismo?: string | null;
  organismo_verificado: boolean;
  comercio?: string | null;
}

export interface SeccionResumen {
  clave: string;
  titulo: string;
  elementos: ElementoCorreo[];
}

export interface ResumenCorreo {
  periodo: "hoy" | "semana";
  desde: string;
  hasta: string;
  secciones: SeccionResumen[];
  otros: Record<string, number>;
  silenciados: number;
}

const isEstado = (v: unknown): v is EstadoCorreo => isRecord(v) && typeof v.conectado === "boolean" && typeof v.configurado === "boolean";
const isResumen = (v: unknown): v is ResumenCorreo => isRecord(v) && Array.isArray(v.secciones);
const isPropuestas = (v: unknown): v is Respuesta<"/correo/propuestas", "get"> => isRecord(v) && Array.isArray(v.propuestas);
const isPropuesta = (v: unknown): v is PropuestaCorreo => isRecord(v) && typeof v.id === "string" && typeof v.estado === "string";
const isUrl = (v: unknown): v is Respuesta<"/correo/conexion", "post"> => isRecord(v) && typeof v.url === "string";
const isSync = (v: unknown): v is SincronizacionCorreo => isRecord(v) && typeof v.nuevos === "number";
const isDesconexion = (v: unknown): v is DesconexionCorreo =>
  isRecord(v) && "revocado_en_google" in v;
const isSilenciado = (v: unknown): v is Respuesta<"/correo/silenciar", "post"> => isRecord(v);

const q = (caseId: string) => `case_id=${encodeURIComponent(caseId)}`;

export function getEstadoCorreo(caseId: string, signal?: AbortSignal): Promise<EstadoCorreo> {
  return request(`/correo/estado?${q(caseId)}`, undefined, isEstado, { signal, method: "GET" });
}

/** The Google consent URL (the caller navigates the whole page to it). */
export function iniciarConexion(caseId: string): Promise<string> {
  return request("/correo/conexion", { case_id: caseId }, isUrl).then((r) => r.url);
}

export function desconectarCorreo(caseId: string, borrarDatos: boolean) {
  return request("/correo/desconectar", { case_id: caseId, borrar_datos: borrarDatos }, isDesconexion);
}

export function sincronizarCorreo(caseId: string): Promise<SincronizacionCorreo> {
  return request("/correo/sincronizar", { case_id: caseId }, isSync, { timeoutMs: 120_000 });
}

export function getResumenCorreo(caseId: string, periodo: "hoy" | "semana", signal?: AbortSignal): Promise<ResumenCorreo> {
  return request(`/correo/resumen?${q(caseId)}&periodo=${periodo}`, undefined, isResumen, { signal, method: "GET" });
}

export function getPropuestasCorreo(caseId: string, signal?: AbortSignal): Promise<PropuestaCorreo[]> {
  return request(`/correo/propuestas?${q(caseId)}`, undefined, isPropuestas, { signal, method: "GET" }).then((r) => r.propuestas);
}

export function decidirPropuesta(id: string, confirmar: boolean): Promise<PropuestaCorreo> {
  return request(`/correo/propuestas/${encodeURIComponent(id)}/${confirmar ? "confirmar" : "rechazar"}`, {}, isPropuesta);
}

/** «No me interesa» for a category or a sender domain (recorded in the decision memory). */
export function silenciar(caseId: string, tipo: "categoria" | "remitente", valor: string) {
  return request("/correo/silenciar", { case_id: caseId, tipo, valor }, isSilenciado);
}
