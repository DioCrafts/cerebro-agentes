// Client for the feedback on the assistant's answers and the review queue that turns mistakes into regression
// cases (asistente/plataforma/api/routers/evaluacion.py).
//
// A vote alone never stores the conversation. Storing it as an evaluation case needs the user to tick the
// opt-in box for THAT answer; the request then carries the version of the consent text they saw. With the
// per-case «aprender de los fallos» consent on, a 👎 and the automatic signals become masked candidates too.
// Everything goes through api.ts's `request`: same session token, timeout and error messages.

import { request } from "./api";
import { isRecord } from "./lib/guards";
import type { Esquemas, Respuesta } from "./api/tipos";

// The kinds of correction the user can pick. The schema sends them as plain strings
// (`ConsentimientoEvaluacion.tipos[].clave`); getConsentimientoEvaluacion drops any this client does not know.
const TIPOS_CORRECCION = [
  "cifra_erronea",
  "modulo_erroneo",
  "hecho_omitido",
  "afirmacion_inventada",
  "suma_indebida",
  "memoria_erronea",
  "otro",
] as const;
export type TipoCorreccion = (typeof TIPOS_CORRECCION)[number];

function esTipoCorreccion(clave: string): clave is TipoCorreccion {
  return (TIPOS_CORRECCION as readonly string[]).includes(clave);
}

// The shapes come from the backend's OpenAPI schema (src/api/esquema.d.ts). POST /evaluacion/feedback and the
// review's «aceptar» take a plain dict, so their bodies (FeedbackPayload, EdicionCaso) are typed here by hand,
// and so are the masked report and traces a candidate stores as dicts (see ContenidoCandidato below).
// `aprendizaje_*`: the per-case consent to learn from detected mistakes.
type ConsentimientoEsquema = Respuesta<"/evaluacion/consentimiento", "get">;
export type ConsentimientoEvaluacion = Omit<ConsentimientoEsquema, "tipos"> & {
  tipos: Array<{ clave: TipoCorreccion; etiqueta: string }>;
};
// `candidato`: a candidate case of the review queue was made from it.
export type FeedbackRespuesta = Respuesta<"/evaluacion/feedback", "post">;

export interface FeedbackPayload {
  caseId: string | null;
  pregunta: string;
  respuesta: string;
  valoracion: "positiva" | "negativa";
  tipo?: TipoCorreccion | null;
  comentario?: string;
  respuestaCorrecta?: string;
  cifraCorrecta?: string;
  // Only with the consent version the user accepted for this answer.
  versionConsentimiento?: string | null;
}

function isConsentimiento(value: unknown): value is ConsentimientoEsquema {
  return isRecord(value) && typeof value.version === "string" && typeof value.texto === "string" && Array.isArray(value.tipos);
}

function isFeedback(value: unknown): value is FeedbackRespuesta {
  return isRecord(value) && typeof value.id === "string" && typeof value.guardado_para_evaluacion === "boolean";
}

function isNull(value: unknown): value is null {
  return value === null;
}

export function getConsentimientoEvaluacion(signal?: AbortSignal): Promise<ConsentimientoEvaluacion> {
  return request("/evaluacion/consentimiento", undefined, isConsentimiento, { signal, method: "GET" }).then((c) => ({
    ...c,
    tipos: c.tipos.flatMap((t) => (isRecord(t) && esTipoCorreccion(t.clave) ? [{ clave: t.clave, etiqueta: t.etiqueta }] : [])),
  }));
}

function vacioANull(texto: string | undefined): string | null {
  const limpio = (texto ?? "").trim();
  return limpio ? limpio : null;
}

export function enviarFeedback(payload: FeedbackPayload): Promise<FeedbackRespuesta> {
  const guardar = Boolean(payload.versionConsentimiento) && payload.caseId !== null;
  return request(
    "/evaluacion/feedback",
    {
      case_id: payload.caseId,
      pregunta: payload.pregunta,
      respuesta: payload.respuesta,
      valoracion: payload.valoracion,
      tipo: payload.valoracion === "negativa" ? (payload.tipo ?? null) : null,
      comentario: payload.valoracion === "negativa" ? vacioANull(payload.comentario) : null,
      respuesta_correcta: payload.valoracion === "negativa" ? vacioANull(payload.respuestaCorrecta) : null,
      cifra_correcta: payload.valoracion === "negativa" ? vacioANull(payload.cifraCorrecta) : null,
      guardar_para_evaluacion: guardar,
      version_consentimiento: guardar ? payload.versionConsentimiento : null,
    },
    isFeedback,
  );
}

export function retirarFeedback(id: string): Promise<null> {
  return request(`/evaluacion/feedback/${encodeURIComponent(id)}`, undefined, isNull, { method: "DELETE" });
}

// --- «Aprender de los fallos» (per case) -------------------------------------------------------------------

export type EstadoAprendizaje = Respuesta<"/evaluacion/aprendizaje", "get">;

function isEstadoAprendizaje(value: unknown): value is EstadoAprendizaje {
  return isRecord(value) && typeof value.activo === "boolean" && typeof value.texto === "string";
}

export function getAprendizaje(caseId: string, signal?: AbortSignal): Promise<EstadoAprendizaje> {
  return request(`/evaluacion/aprendizaje?case_id=${encodeURIComponent(caseId)}`, undefined, isEstadoAprendizaje, {
    signal,
    method: "GET",
  });
}

export function cambiarAprendizaje(caseId: string, activo: boolean, version: string | null): Promise<EstadoAprendizaje> {
  return request("/evaluacion/aprendizaje", { case_id: caseId, activo, version: activo ? version : null }, isEstadoAprendizaje, {
    method: "PUT",
  });
}

// --- The review queue ----------------------------------------------------------------------------------------

export type ColaRevision = Respuesta<"/evaluacion/revision", "get">;
export type GrupoResumen = ColaRevision["grupos"][number];
export type CategoriaInfo = ColaRevision["categorias"][number];
type DetalleCandidatoEsquema = Respuesta<"/evaluacion/revision/{candidato_id}", "get">;
export type EstadoCandidato = DetalleCandidatoEsquema["candidato"]["estado"];
export type CambioRedaccion = Esquemas["CambioRedaccion"];

/** The brain's report as stored in a candidate (masked); only what the panel shows is typed. */
export interface CerebroCandidato {
  intencion: {
    tipo: string;
    resumen?: string;
    aclaracion?: string | null;
    confianza: number;
    encargos: Array<{ id: string; especialista: string; pregunta: string; depende_de: string[] }>;
  };
  ruta: string;
  informes: Array<{
    encargo_id: string;
    especialista: string;
    nombre: string;
    estado: string;
    modo?: string | null;
    cifras: Array<{ id: string; concepto: string; literal?: string; valor: string; unidad?: string | null }>;
  }>;
  peticiones: Array<{ id: string; solicitante: string; destinatario: string; dato: string }>;
  puerta?: { violaciones: Array<{ tipo: string; literal: string; detalle: string }> } | null;
  recuerdos_usados: string[];
}

/** A candidate's stored content. The backend keeps the brain's report, the signals, the tool trace and the snapshot
 * as plain dicts (`dict[str, Any]` in the schema); only the fields the panel reads are typed here. */
export type ContenidoCandidato = Omit<
  Esquemas["ContenidoCandidato"],
  "cerebro" | "cerebro_resumen" | "senales" | "tool_trace" | "instantanea"
> & {
  cerebro: CerebroCandidato | null;
  // Only the summary the audit trail kept, when there is no full report.
  cerebro_resumen: { ruta?: string; tipo?: string; especialistas?: string[]; violaciones_cifras?: number } | null;
  senales: Array<{ tipo: string; detalle: string }>;
  tool_trace: Array<{ tool: string; agent?: string; is_error?: boolean }>;
  instantanea: { hechos?: Record<string, unknown> };
};

export type Candidato = Omit<DetalleCandidatoEsquema["candidato"], "contenido"> & { contenido: ContenidoCandidato };
export type DetalleCandidato = Omit<DetalleCandidatoEsquema, "candidato"> & { candidato: Candidato };

export interface CifraEsperadaEdicion {
  herramienta: string;
  argumentos: Record<string, unknown>;
  referencia?: string | null;
  especialista?: string | null;
}

export interface ComportamientoEsperado {
  especialistas_obligatorios: string[];
  especialistas_prohibidos: string[];
  citas_minimas: number;
  cifras_prohibidas: string[];
  cifra_esperada: CifraEsperadaEdicion | null;
  debe_abstenerse: boolean;
  debe_aclarar: boolean;
  puerta_sin_violaciones: boolean;
  textos_obligatorios: string[];
  textos_prohibidos: string[];
}

export interface EdicionCaso {
  categoria: string;
  descripcion: string;
  pregunta: string | null;
  esperado: ComportamientoEsperado;
  aunque_no_reproduzca: boolean;
}

export type Aceptado = Respuesta<"/evaluacion/revision/{candidato_id}/aceptar", "post">;

function isCola(value: unknown): value is ColaRevision {
  return isRecord(value) && Array.isArray(value.grupos) && Array.isArray(value.categorias);
}

function isDetalle(value: unknown): value is DetalleCandidato {
  return isRecord(value) && isRecord(value.candidato) && Array.isArray(value.categorias);
}

function isAceptado(value: unknown): value is Aceptado {
  return isRecord(value) && typeof value.id === "string" && Array.isArray(value.expectativas);
}

function isAceptados(value: unknown): value is Respuesta<"/evaluacion/revision/aceptados", "get"> {
  return isRecord(value) && Array.isArray(value.casos);
}

function isRechazo(value: unknown): value is Respuesta<"/evaluacion/revision/{candidato_id}/rechazar", "post"> & { rechazados: number } {
  return isRecord(value) && typeof value.rechazados === "number";
}

export function getColaRevision(estado: EstadoCandidato, signal?: AbortSignal): Promise<ColaRevision> {
  return request(`/evaluacion/revision?estado=${estado}`, undefined, isCola, { signal, method: "GET" });
}

export function getCandidato(id: string, signal?: AbortSignal): Promise<DetalleCandidato> {
  return request(`/evaluacion/revision/${encodeURIComponent(id)}`, undefined, isDetalle, { signal, method: "GET" });
}

export function aceptarCandidato(id: string, edicion: EdicionCaso): Promise<Aceptado> {
  return request(`/evaluacion/revision/${encodeURIComponent(id)}/aceptar`, edicion, isAceptado);
}

export function rechazarCandidato(id: string, motivo: string): Promise<{ rechazados: number }> {
  return request(`/evaluacion/revision/${encodeURIComponent(id)}/rechazar`, { motivo }, isRechazo);
}

export function getAceptados(signal?: AbortSignal): Promise<{ casos: Aceptado[] }> {
  return request("/evaluacion/revision/aceptados", undefined, isAceptados, { signal, method: "GET" });
}

export function borrarAceptado(id: string): Promise<null> {
  return request(`/evaluacion/revision/aceptados/${encodeURIComponent(id)}`, undefined, isNull, { method: "DELETE" });
}
