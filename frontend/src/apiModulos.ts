// Client for the household modules (asistente/plataforma/api/routers/modulos.py): which modules exist and
// which are on for this user, the pending actions the specialists prepared, and running a module's
// tool or document reader from the UI.
//
// Everything goes through api.ts's `request`: same session token, timeout and error messages.

import { request } from "./api";
import { isRecord } from "./lib/guards";
import type { Esquemas, Respuesta } from "./api/tipos";

// The shapes come from the backend's OpenAPI schema (src/api/esquema.d.ts), except a document reading's
// `propuesta`, which the backend sends as a plain dict: its fields are typed here by hand (CampoPropuesto).
export type ModulosResponse = Respuesta<"/modulos", "get">;
export type ModuloInfo = ModulosResponse["modulos"][number];
export type HechoInfo = ModuloInfo["hechos"][number];
export type HerramientaInfo = ModuloInfo["herramientas"][number];
export type FuenteInfo = ModuloInfo["fuentes"][number];
export type AccionPendiente = Esquemas["AccionPendiente"];
export type EstadoAccion = AccionPendiente["estado"];
export type EjecucionResponse = Respuesta<"/modulos/{clave}/herramientas/{nombre}", "post">;
export type ResultadoHerramienta = NonNullable<EjecucionResponse["resultado"]>;

export interface CampoPropuesto {
  campo: string;
  valor: string;
  evidencia: string;
  origen?: string;
}

export type DocumentoModuloResponse = Omit<Respuesta<"/modulos/{clave}/documento", "post">, "propuesta"> & {
  propuesta: { campos?: CampoPropuesto[]; avisos?: string[]; descartados?: string[] } & Record<string, unknown>;
};

function isModulos(value: unknown): value is ModulosResponse {
  return isRecord(value) && typeof value.configurable === "boolean" && Array.isArray(value.modulos);
}

function isAccion(value: unknown): value is AccionPendiente {
  return isRecord(value) && typeof value.id === "string" && typeof value.estado === "string";
}

function isAcciones(value: unknown): value is Respuesta<"/acciones", "get"> {
  return isRecord(value) && Array.isArray(value.acciones);
}

function isEjecucion(value: unknown): value is EjecucionResponse {
  return isRecord(value) && "resultado" in value && "accion" in value;
}

function isDocumento(value: unknown): value is DocumentoModuloResponse {
  return isRecord(value) && typeof value.modulo === "string" && isRecord(value.propuesta);
}

/** The modules this service offers and whether each one is on for the signed-in user. */
export function getModulos(signal?: AbortSignal): Promise<ModulosResponse> {
  return request("/modulos", undefined, isModulos, { signal, method: "GET" });
}

/** Switch a module on or off; `borrarDatos` (only when switching off) erases its facts too. */
export function setModulo(clave: string, activo: boolean, borrarDatos = false): Promise<ModulosResponse> {
  return request(`/me/modulos/${encodeURIComponent(clave)}`, { activo, borrar_datos: borrarDatos }, isModulos, { method: "PUT" });
}

export function getAcciones(caseId: string, signal?: AbortSignal): Promise<AccionPendiente[]> {
  return request(`/acciones?case_id=${encodeURIComponent(caseId)}`, undefined, isAcciones, { signal, method: "GET" }).then(
    (resp) => resp.acciones,
  );
}

export function confirmarAccion(id: string): Promise<AccionPendiente> {
  return request(`/acciones/${encodeURIComponent(id)}/confirmar`, {}, isAccion);
}

export function rechazarAccion(id: string): Promise<AccionPendiente> {
  return request(`/acciones/${encodeURIComponent(id)}/rechazar`, {}, isAccion);
}

/** Run one of a module's tools for this case (acting tools only become pending actions). */
export function runModuleTool(opts: {
  clave: string;
  herramienta: string;
  caseId: string;
  argumentos: Record<string, unknown>;
}): Promise<EjecucionResponse> {
  return request(
    `/modulos/${encodeURIComponent(opts.clave)}/herramientas/${encodeURIComponent(opts.herramienta)}`,
    { case_id: opts.caseId, argumentos: opts.argumentos },
    isEjecucion,
  );
}

/** A module reads a document and PROPOSES data; nothing is stored. */
export function readModuleDocument(opts: {
  clave: string;
  caseId: string;
  filename: string;
  contentBase64: string;
}): Promise<DocumentoModuloResponse> {
  return request(
    `/modulos/${encodeURIComponent(opts.clave)}/documento`,
    { case_id: opts.caseId, filename: opts.filename, content_base64: opts.contentBase64 },
    isDocumento,
    { timeoutMs: 120_000 },
  );
}

/** The module a notification belongs to: the tax kinds are "fiscal", the weekly digest has none. */
export function moduloDeKind(kind: string): string | null {
  if (kind.startsWith("modulo:")) return kind.slice("modulo:".length);
  return kind === "resumen_semanal" ? null : "fiscal";
}

/** A tax fact a module can fill in; nothing is written until the user accepts it. */
export type PropuestaHecho = Respuesta<"/propuestas", "get">["propuestas"][number];

function isPropuestas(value: unknown): value is Respuesta<"/propuestas", "get"> {
  return isRecord(value) && Array.isArray(value.propuestas);
}

export function getPropuestas(caseId: string, signal?: AbortSignal): Promise<PropuestaHecho[]> {
  return request(`/propuestas?case_id=${encodeURIComponent(caseId)}`, undefined, isPropuestas, { signal, method: "GET" }).then(
    (resp) => resp.propuestas,
  );
}

// Capabilities (asistente/plataforma/api/routers/capacidades.py): means any module can use (mail, browser, documents,
// research), shown apart from the modules with the user's consent.

export type CapacidadesResponse = Respuesta<"/capacidades", "get">;
// `modulo`: a module with the same key is its chat front; its switch is the module's (in «Módulos»).
export type CapacidadInfo = CapacidadesResponse["capacidades"][number];
export type OperacionCapacidadInfo = CapacidadInfo["operaciones"][number];
export type Efecto = OperacionCapacidadInfo["efecto"];

function isCapacidades(value: unknown): value is CapacidadesResponse {
  return isRecord(value) && typeof value.configurable === "boolean" && Array.isArray(value.capacidades);
}

/** The capabilities this service offers and whether the signed-in user consented to each. */
export function getCapacidades(signal?: AbortSignal): Promise<CapacidadesResponse> {
  return request("/capacidades", undefined, isCapacidades, { signal, method: "GET" });
}

/** Give or withdraw consent to a capability that is not also a module (those switch in their module). */
export function setCapacidad(clave: string, activa: boolean): Promise<CapacidadesResponse> {
  return request(`/me/capacidades/${encodeURIComponent(clave)}`, { activa }, isCapacidades, { method: "PUT" });
}
