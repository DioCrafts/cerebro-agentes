// Client for the messaging gateway (asistente/plataforma/api/routers/mensajeria.py): the channel's state and the
// bot's address, a one-time pairing code, the paired chats (pause notices, unpair).
// Everything goes through api.ts's `request`: same session token, timeout and error messages.

import { request } from "./api";

export interface CanalMensajeria {
  canal: string;
  nombre: string;
  conectado: boolean;
  detalle: string;
  /** What a phone scans or opens to reach the bot (SimpleX: its contact address). */
  direccion: string | null;
  actualizado: string | null;
}

export interface EstadoMensajeria {
  activa: boolean;
  canales: CanalMensajeria[];
  dispositivos: number;
  max_dispositivos: number;
  codigo_caduca: string | null;
}

export interface CodigoEmparejamiento {
  /** Shown once: the server keeps only its hash. */
  codigo: string;
  caduca: string;
  segundos: number;
  canal: string;
  nombre_canal: string;
  direccion: string | null;
  conectado: boolean;
  instrucciones: string;
}

export interface DispositivoMensajeria {
  id: string;
  canal: string;
  nombre_canal: string;
  nombre_mostrado: string | null;
  case_id: string;
  creado: string;
  ultimo_uso: string | null;
  avisos_en_pausa: boolean;
}

function esObjeto(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null;
}

function esEstado(v: unknown): v is EstadoMensajeria {
  return esObjeto(v) && typeof v.activa === "boolean" && Array.isArray(v.canales) && typeof v.dispositivos === "number";
}

function esCodigo(v: unknown): v is CodigoEmparejamiento {
  return esObjeto(v) && typeof v.codigo === "string" && typeof v.segundos === "number" && typeof v.caduca === "string";
}

function esDispositivo(v: unknown): v is DispositivoMensajeria {
  return esObjeto(v) && typeof v.id === "string" && typeof v.avisos_en_pausa === "boolean";
}

function esLista(v: unknown): v is { dispositivos: DispositivoMensajeria[] } {
  return esObjeto(v) && Array.isArray(v.dispositivos) && v.dispositivos.every(esDispositivo);
}

function esNada(v: unknown): v is null {
  return v === null;
}

export function getEstadoMensajeria(signal?: AbortSignal): Promise<EstadoMensajeria> {
  return request("/mensajeria/estado", undefined, esEstado, { signal, method: "GET" });
}

/** A one-time pairing code for this case (valid a few minutes; a new one voids the previous one). */
export function pedirCodigo(caseId: string, canal?: string): Promise<CodigoEmparejamiento> {
  return request("/mensajeria/emparejar", { case_id: caseId, ...(canal ? { canal } : {}) }, esCodigo);
}

export function getDispositivos(signal?: AbortSignal): Promise<DispositivoMensajeria[]> {
  return request("/mensajeria/dispositivos", undefined, esLista, { signal, method: "GET" }).then((r) => r.dispositivos);
}

export function pausarAvisos(id: string, pausado: boolean): Promise<DispositivoMensajeria> {
  return request(`/mensajeria/dispositivos/${encodeURIComponent(id)}/pausa`, { pausado }, esDispositivo, { method: "PUT" });
}

/** Unpair a chat: the server forgets it now; the chat gets a goodbye and the contact is removed on the channel. */
export function desvincular(id: string): Promise<null> {
  return request(`/mensajeria/dispositivos/${encodeURIComponent(id)}`, undefined, esNada, { method: "DELETE" });
}
