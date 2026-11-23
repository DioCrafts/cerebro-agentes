// Client for the browser agent (asistente/plataforma/api/routers/navegador.py): which tasks the service offers,
// starting one after the user's acknowledgement, following it live, stopping and discarding it.
//
// Credentials never go through here: the user logs in in the browser window itself (mode "ventana") or
// hands over a session for one run (mode "sesion", an advanced option sent once and never stored).

import { request } from "./api";
import { isRecord } from "./lib/guards";
import type { Peticion, Respuesta } from "./api/tipos";

// The shapes come from the backend's OpenAPI schema (src/api/esquema.d.ts).
type EstadoNavegadorEsquema = Respuesta<"/navegador/estado", "get">;
export type GuionInfo = EstadoNavegadorEsquema["tareas"][number];
export type ParametroTarea = GuionInfo["parametros"][number];
export type TareaInfo = Respuesta<"/navegador/tareas/{tarea_id}", "get">;
export type EventoTarea = TareaInfo["eventos"][number];
// `sesion`: Playwright storage_state, only for mode "sesion".
export type InicioTarea = Peticion<"/navegador/tareas/{tarea_id}/iniciar", "post">;
export type ModoNavegador = NonNullable<InicioTarea["modo"]>;
// The schema sends `modos` as plain strings; getEstadoNavegador keeps the ones this client can start.
export type EstadoNavegador = Omit<EstadoNavegadorEsquema, "modos"> & { modos: ModoNavegador[] };

const MODOS: readonly string[] = ["ventana", "sesion"] satisfies ModoNavegador[];
const esModo = (modo: unknown): modo is ModoNavegador => typeof modo === "string" && MODOS.includes(modo);

export const ESTADOS_ACTIVOS = new Set(["en_curso", "esperando_login", "trabajando"]);

function isEstado(value: unknown): value is EstadoNavegadorEsquema {
  return isRecord(value) && typeof value.activo === "boolean" && Array.isArray(value.tareas);
}

export function isTarea(value: unknown): value is TareaInfo {
  return isRecord(value) && typeof value.id === "string" && typeof value.estado === "string" && Array.isArray(value.eventos);
}

function isTareas(value: unknown): value is Respuesta<"/navegador/tareas", "get"> {
  return isRecord(value) && Array.isArray(value.tareas);
}

function isCaptura(value: unknown): value is Respuesta<"/navegador/tareas/{tarea_id}/captura", "get"> {
  return isRecord(value) && typeof value.png_base64 === "string";
}

export function getEstadoNavegador(signal?: AbortSignal): Promise<EstadoNavegador> {
  return request("/navegador/estado", undefined, isEstado, { method: "GET", signal }).then((e) => ({
    ...e,
    modos: Array.isArray(e.modos) ? e.modos.filter(esModo) : [],
  }));
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

/** The task's live stream (Server-Sent Events `tarea`, then `fin` once it stops). */
export function rutaEventosTarea(id: string): string {
  return `/navegador/tareas/${encodeURIComponent(id)}/eventos`;
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
