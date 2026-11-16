// Client for the browser agent (asistente/plataforma/api/routers/navegador.py): which tasks the service offers,
// starting one after the user's acknowledgement, following it live, stopping and discarding it.
//
// Credentials never go through here: the user logs in in the browser window itself (mode "ventana") or
// hands over a session for one run (mode "sesion", an advanced option sent once and never stored).

import { request } from "./api";

export type ModoNavegador = "ventana" | "sesion";

export interface ParametroTarea {
  nombre: string;
  tipo: "fecha" | "texto" | "cups" | "opcion";
  etiqueta: string;
  obligatorio: boolean;
  opciones: string[];
  personal: boolean;
}

export interface GuionInfo {
  clave: string;
  version: number;
  titulo: string;
  descripcion: string;
  verificado_contra_sitio_real: boolean;
  nota_verificacion: string;
  demostracion: boolean;
  sitios: string[];
  parametros: ParametroTarea[];
  final: "descarga" | "detener";
  modulo_destino?: string | null;
}

export interface EstadoNavegador {
  activo: boolean;
  modos: ModoNavegador[];
  consentimiento: boolean;
  texto_consentimiento: string;
  texto_version: string;
  tareas: GuionInfo[];
  max_tareas_dia: number;
  usadas_hoy: number;
}

export interface EventoTarea {
  momento: string;
  tipo: string;
  texto: string;
  paso?: string | null;
}

export interface TareaInfo {
  id: string;
  case_id: string;
  guion: string;
  version: number;
  titulo: string;
  modo: ModoNavegador | null;
  estado: string;
  creada: string;
  actualizada: string;
  terminada?: string | null;
  parametros: Record<string, string>;
  eventos: EventoTarea[];
  pasos: Array<{ id: string; descripcion: string; estado: string }>;
  tiene_captura: boolean;
  resumen_envio: Array<{ etiqueta: string; valor: string }>;
  mensaje?: string | null;
  importacion?: string | null;
  importacion_error: boolean;
  accion_id?: string | null;
  origenes_bloqueados: string[];
}

export const ESTADOS_ACTIVOS = new Set(["en_curso", "esperando_login", "trabajando"]);
export const ESTADOS_TERMINADOS = new Set([
  "descargado",
  "importado",
  "detenido_antes_de_enviar",
  "fallida",
  "cancelada",
  "login_no_completado",
  "cerrada",
]);

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isEstado(value: unknown): value is EstadoNavegador {
  return isRecord(value) && typeof value.activo === "boolean" && Array.isArray(value.tareas);
}

function isTarea(value: unknown): value is TareaInfo {
  return isRecord(value) && typeof value.id === "string" && typeof value.estado === "string" && Array.isArray(value.eventos);
}

function isTareas(value: unknown): value is { case_id: string; tareas: TareaInfo[] } {
  return isRecord(value) && Array.isArray(value.tareas);
}

function isCaptura(value: unknown): value is { png_base64: string; momento: string | null } {
  return isRecord(value) && typeof value.png_base64 === "string";
}

export function getEstadoNavegador(signal?: AbortSignal): Promise<EstadoNavegador> {
  return request("/navegador/estado", undefined, isEstado, { method: "GET", signal });
}

export interface InicioTarea {
  modo: ModoNavegador;
  acepto: boolean;
  texto_version: string;
  // Playwright storage_state, only for mode "sesion".
  sesion?: Record<string, unknown> | null;
}

export function crearTarea(
  caseId: string,
  guion: string,
  parametros: Record<string, string>,
  inicio: InicioTarea,
): Promise<TareaInfo> {
  return request("/navegador/tareas", { case_id: caseId, guion, parametros, iniciar: true, ...inicio }, isTarea, { timeoutMs: 30_000 });
}

export function iniciarTarea(id: string, inicio: InicioTarea): Promise<TareaInfo> {
  return request(`/navegador/tareas/${encodeURIComponent(id)}/iniciar`, inicio, isTarea, { timeoutMs: 30_000 });
}

export function getTareas(caseId: string, signal?: AbortSignal): Promise<TareaInfo[]> {
  return request(`/navegador/tareas?case_id=${encodeURIComponent(caseId)}`, undefined, isTareas, { method: "GET", signal }).then(
    (r) => r.tareas,
  );
}

export function getTarea(id: string, signal?: AbortSignal): Promise<TareaInfo> {
  return request(`/navegador/tareas/${encodeURIComponent(id)}`, undefined, isTarea, { method: "GET", signal });
}

/** The latest screenshot as a data: URL (kept only in memory by the page). */
export function getCaptura(id: string, signal?: AbortSignal): Promise<string> {
  return request(`/navegador/tareas/${encodeURIComponent(id)}/captura`, undefined, isCaptura, { method: "GET", signal }).then(
    (r) => `data:image/png;base64,${r.png_base64}`,
  );
}

export function detenerTarea(id: string): Promise<TareaInfo> {
  return request(`/navegador/tareas/${encodeURIComponent(id)}/detener`, {}, isTarea);
}

export function cerrarTarea(id: string): Promise<TareaInfo> {
  return request(`/navegador/tareas/${encodeURIComponent(id)}/cerrar`, {}, isTarea);
}
