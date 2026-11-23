// Client for the mail module (asistente/plataforma/api/routers/correo.py): Gmail in read-only mode.
//
// The Google consent screen is reached with a full-page navigation to the URL the API returns; Google
// sends the browser back to the API, which stores the (encrypted) permission and redirects here with
// `?correo=conectado|cancelado|error`. No Google credential ever passes through this code.

import { request } from "./api";
import { isRecord } from "./lib/guards";

export interface EstadoCorreo {
  case_id: string;
  configurado: boolean;
  activo: boolean;
  conectado: boolean;
  estado?: "conectada" | "reconectar" | null;
  cuenta?: string | null;
  alcance: string[];
  conectada_el?: string | null;
  ultima_sincronizacion?: string | null;
  ultimo_error?: string | null;
  elementos: number;
  propuestas_pendientes: number;
  ventana_dias: number;
  solo_lectura: boolean;
}

export interface CampoCorreo {
  campo: string;
  valor: string;
  literal: string;
  fragmento: string;
}

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

export type EstadoPropuesta = "pendiente" | "aplicando" | "confirmada" | "rechazada" | "fallida";

export interface PropuestaCorreo {
  id: string;
  case_id: string;
  elemento_id: string;
  destino: string;
  tipo: "herramienta" | "hechos" | "nota";
  herramienta?: string | null;
  argumentos: Record<string, unknown>;
  hechos: Record<string, unknown>;
  titulo: string;
  explicacion: string;
  evidencias: CampoCorreo[];
  origen: { dominio: string; asunto?: string | null; fecha: string };
  estado: EstadoPropuesta;
  resultado?: string | null;
}

export interface SincronizacionCorreo {
  modo: string;
  leidos: number;
  nuevos: number;
  completos: number;
  categorias: Record<string, number>;
  propuestas: number;
  purgados: number;
}

const isEstado = (v: unknown): v is EstadoCorreo => isRecord(v) && typeof v.conectado === "boolean" && typeof v.configurado === "boolean";
const isResumen = (v: unknown): v is ResumenCorreo => isRecord(v) && Array.isArray(v.secciones);
const isPropuestas = (v: unknown): v is { propuestas: PropuestaCorreo[] } => isRecord(v) && Array.isArray(v.propuestas);
const isPropuesta = (v: unknown): v is PropuestaCorreo => isRecord(v) && typeof v.id === "string" && typeof v.estado === "string";
const isUrl = (v: unknown): v is { url: string } => isRecord(v) && typeof v.url === "string";
const isSync = (v: unknown): v is SincronizacionCorreo => isRecord(v) && typeof v.nuevos === "number";
const isDesconexion = (v: unknown): v is { revocado_en_google: boolean | null; datos_borrados: number } =>
  isRecord(v) && "revocado_en_google" in v;
const isObjeto = (v: unknown): v is Record<string, unknown> => isRecord(v);

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
  return request("/correo/silenciar", { case_id: caseId, tipo, valor }, isObjeto);
}
