// Client for the long-running tasks (asistente/plataforma/api/routers/tareas.py): goals of several steps over several days.
//
// The shapes come from the backend's OpenAPI schema (src/api/esquema.d.ts). A pending ACTION is never
// confirmed here: it goes through POST /acciones/{id}/confirmar (apiModulos.ts), its own button.
// Everything goes through api.ts's `request`: same session token, timeout and error messages.

import { request } from "./api";
import { isRecord } from "./lib/guards";
import type { Peticion, Respuesta } from "./api/tipos";

// The task and its history come from the backend's OpenAPI schema (src/api/esquema.d.ts). The summary
// (asistente/nucleo/tareas/servicio.py -> resumen), the list of tasks and the templates travel as plain dicts
// (`dict[str, Any]` in the schema), so ResumenTarea, Espera and Plantilla are typed here by hand.
type TareaDetalleEsquema = Respuesta<"/tareas/{tarea_id}", "get">;
export type Tarea = TareaDetalleEsquema["tarea"];
export type Paso = Tarea["pasos"][number];
export type EntradaHistorial = TareaDetalleEsquema["historial"][number];
export type EstadoTarea = Tarea["estado"];
export type TipoPaso = Paso["tipo"];
export type EstadoPaso = Paso["estado"];
export type MotivoEspera = NonNullable<Paso["espera_actual"]>["motivo"];
export type NuevaTarea = Peticion<"/tareas", "post">;

export interface Espera {
  paso: string;
  titulo: string;
  motivo: MotivoEspera;
  detalle: string;
  hasta: string | null;
  accion_id: string | null;
  reintentar: boolean;
}

export interface ResumenTarea {
  id: string;
  ref: string;
  titulo: string;
  objetivo: string;
  plantilla: string | null;
  estado: EstadoTarea;
  estado_texto: string;
  pasos_hechos: number;
  pasos_total: number;
  esperando: Espera[];
  proxima_revision: string | null;
  vence: string | null;
  creada: string;
}

export type TareaDetalle = Omit<TareaDetalleEsquema, "resumen"> & { resumen: ResumenTarea };
export type ListadoTareas = Omit<Respuesta<"/tareas", "get">, "tareas"> & { tareas: ResumenTarea[] };

export interface Plantilla {
  id: string;
  version: number;
  titulo: string;
  descripcion: string;
  pasos: Array<{ id: string; titulo: string; tipo: TipoPaso }>;
}

export interface PlantillasResponse {
  plantillas: Plantilla[];
}

function esDetalle(v: unknown): v is TareaDetalle {
  return isRecord(v) && isRecord(v.tarea) && Array.isArray((v.tarea).pasos) && isRecord(v.resumen);
}

function esListado(v: unknown): v is ListadoTareas {
  return isRecord(v) && Array.isArray(v.tareas);
}

function esPlantillas(v: unknown): v is PlantillasResponse {
  return isRecord(v) && Array.isArray(v.plantillas);
}

export function getPlantillas(signal?: AbortSignal): Promise<PlantillasResponse> {
  return request("/tareas/plantillas", undefined, esPlantillas, { method: "GET", signal });
}

export function getTareas(caseId: string, signal?: AbortSignal): Promise<ListadoTareas> {
  return request(`/tareas?case_id=${encodeURIComponent(caseId)}`, undefined, esListado, { method: "GET", signal });
}

export function getTarea(id: string, signal?: AbortSignal): Promise<TareaDetalle> {
  return request(`/tareas/${encodeURIComponent(id)}`, undefined, esDetalle, { method: "GET", signal });
}

export function crearTarea(body: NuevaTarea): Promise<TareaDetalle> {
  return request("/tareas", body, esDetalle);
}

export type OperacionTarea = "aceptar" | "avanzar" | "pausar" | "reanudar" | "reintentar";

export function operarTarea(id: string, operacion: OperacionTarea): Promise<TareaDetalle> {
  return request(`/tareas/${encodeURIComponent(id)}/${operacion}`, undefined, esDetalle);
}

export function cancelarTarea(id: string, motivo: string | null): Promise<TareaDetalle> {
  return request(`/tareas/${encodeURIComponent(id)}/cancelar`, { motivo: motivo || null }, esDetalle);
}

export function confirmarPaso(id: string, paso: string, datos: { fecha?: string | null; nota?: string } = {}): Promise<TareaDetalle> {
  return request(`/tareas/${encodeURIComponent(id)}/pasos/${encodeURIComponent(paso)}/confirmar`, datos, esDetalle);
}

export function omitirPaso(id: string, paso: string): Promise<TareaDetalle> {
  return request(`/tareas/${encodeURIComponent(id)}/pasos/${encodeURIComponent(paso)}/omitir`, undefined, esDetalle);
}

export const ESTADO_TAREA: Record<EstadoTarea, string> = {
  propuesta: "Propuesta",
  activa: "En marcha",
  pausada: "En pausa",
  esperando_usuario: "Te espera",
  esperando_evento: "Esperando una fecha",
  completada: "Completada",
  cancelada: "Cancelada",
  fallida: "Con un fallo",
};

export const ESTADO_PASO: Record<EstadoPaso, string> = {
  pendiente: "Pendiente",
  esperando: "Esperando",
  hecho: "Hecho",
  omitido: "Saltado",
  fallido: "Ha fallado",
  cancelado: "Cancelado",
};

export const EVENTO_HISTORIAL: Record<string, string> = {
  propuesta: "Propuesta",
  aceptada: "La aceptaste",
  paso_iniciado: "Empezó un paso",
  paso_hecho: "Paso hecho",
  paso_omitido: "Paso saltado",
  paso_esperando: "Paso esperando",
  paso_fallido: "Paso fallido",
  paso_reintento: "Reintento programado",
  paso_confirmado: "Confirmaste un paso",
  paso_reintento_pedido: "Pediste reintentar un paso",
  paso_reanudado: "Paso reanudado",
  accion_pendiente: "Acción pendiente de tu confirmación",
  aviso: "Aviso en tu bandeja",
  estado: "Cambio de estado",
  pausada: "La pausaste",
  reanudada: "La reanudaste",
  reintento_pedido: "Pediste reintentar",
  cancelada: "La cancelaste",
  decision_registrada: "Se recordará tu motivo",
  acciones_rechazadas: "Se rechazaron sus acciones pendientes",
};
