// Client for the long-running tasks (hacienda_gpt/api/routers/tareas.py): goals of several steps over several days.
//
// Shapes mirror hacienda_gpt/tareas/modelo.py (Tarea, Paso) and servicio.resumen. A pending ACTION is never
// confirmed here: it goes through POST /acciones/{id}/confirmar (apiModulos.ts), its own button.
// Everything goes through api.ts's `request`: same session token, timeout and error messages.

import { request } from "./api";

export type EstadoTarea =
  | "propuesta"
  | "activa"
  | "pausada"
  | "esperando_usuario"
  | "esperando_evento"
  | "completada"
  | "cancelada"
  | "fallida";
export type TipoPaso = "calculo" | "consulta" | "capacidad" | "espera" | "confirmacion_usuario" | "accion" | "nota";
export type EstadoPaso = "pendiente" | "esperando" | "hecho" | "omitido" | "fallido" | "cancelado";
export type MotivoEspera = "usuario" | "fecha" | "evento" | "accion" | "modulo";

export interface Paso {
  id: string;
  titulo: string;
  descripcion: string;
  tipo: TipoPaso;
  depende_de: string[];
  opcional: boolean;
  estado: EstadoPaso;
  espera_actual: {
    motivo: MotivoEspera;
    desde: string;
    hasta: string | null;
    accion_id: string | null;
    detalle: string;
    reintentar: boolean;
  } | null;
  resultado: { resumen: string; evidencias: string[]; fecha: string | null; borrador: string | null } | null;
  intentos: number;
  error: string | null;
  terminado: string | null;
}

export interface Tarea {
  id: string;
  case_id: string;
  titulo: string;
  objetivo: string;
  origen: "plantilla" | "plan";
  plantilla: string | null;
  estado: EstadoTarea;
  creada: string;
  vence: string | null;
  proxima_revision: string | null;
  pasos: Paso[];
  motivo_cancelacion: string | null;
}

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

export interface EntradaHistorial {
  seq: number;
  momento: string;
  evento: string;
  paso_id: string | null;
}

export interface TareaDetalle {
  tarea: Tarea;
  resumen: ResumenTarea;
  historial: EntradaHistorial[];
  avisos: string[];
}

export interface Plantilla {
  id: string;
  version: number;
  titulo: string;
  descripcion: string;
  pasos: Array<{ id: string; titulo: string; tipo: TipoPaso }>;
}

const esObjeto = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null;

function esDetalle(v: unknown): v is TareaDetalle {
  return esObjeto(v) && esObjeto(v.tarea) && Array.isArray((v.tarea as Record<string, unknown>).pasos) && esObjeto(v.resumen);
}

function esListado(v: unknown): v is { case_id: string; tareas: ResumenTarea[] } {
  return esObjeto(v) && Array.isArray(v.tareas);
}

function esPlantillas(v: unknown): v is { plantillas: Plantilla[] } {
  return esObjeto(v) && Array.isArray(v.plantillas);
}

export function getPlantillas(signal?: AbortSignal): Promise<{ plantillas: Plantilla[] }> {
  return request("/tareas/plantillas", undefined, esPlantillas, { method: "GET", signal });
}

export function getTareas(caseId: string, signal?: AbortSignal): Promise<{ case_id: string; tareas: ResumenTarea[] }> {
  return request(`/tareas?case_id=${encodeURIComponent(caseId)}`, undefined, esListado, { method: "GET", signal });
}

export function getTarea(id: string, signal?: AbortSignal): Promise<TareaDetalle> {
  return request(`/tareas/${encodeURIComponent(id)}`, undefined, esDetalle, { method: "GET", signal });
}

export function crearTarea(body: {
  case_id: string;
  plantilla: string;
  objetivo: string;
  parametros?: Record<string, unknown>;
  aceptar?: boolean;
}): Promise<TareaDetalle> {
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
