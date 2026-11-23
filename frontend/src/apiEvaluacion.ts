// Client for the feedback on the assistant's answers and the review queue that turns mistakes into regression
// cases (asistente/plataforma/api/routers/evaluacion.py).
//
// A vote alone never stores the conversation. Storing it as an evaluation case needs the user to tick the
// opt-in box for THAT answer; the request then carries the version of the consent text they saw. With the
// per-case «aprender de los fallos» consent on, a 👎 and the automatic signals become masked candidates too.
// Everything goes through api.ts's `request`: same session token, timeout and error messages.

import { request } from "./api";
import { isRecord } from "./lib/guards";

export type TipoCorreccion =
  | "cifra_erronea"
  | "modulo_erroneo"
  | "hecho_omitido"
  | "afirmacion_inventada"
  | "suma_indebida"
  | "memoria_erronea"
  | "otro";

export interface ConsentimientoEvaluacion {
  version: string;
  texto: string;
  tipos: Array<{ clave: TipoCorreccion; etiqueta: string }>;
  // The per-case consent to learn from detected mistakes (older backends do not send it).
  aprendizaje_version?: string;
  aprendizaje_texto?: string;
}

export interface FeedbackRespuesta {
  id: string;
  guardado_para_evaluacion: boolean;
  estado: string;
  etiquetas: string[];
  mensaje: string;
  // A candidate case of the review queue was made from it.
  candidato?: boolean;
}

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

function isConsentimiento(value: unknown): value is ConsentimientoEvaluacion {
  return isRecord(value) && typeof value.version === "string" && typeof value.texto === "string" && Array.isArray(value.tipos);
}

function isFeedback(value: unknown): value is FeedbackRespuesta {
  return isRecord(value) && typeof value.id === "string" && typeof value.guardado_para_evaluacion === "boolean";
}

function isNull(value: unknown): value is null {
  return value === null;
}

export function getConsentimientoEvaluacion(signal?: AbortSignal): Promise<ConsentimientoEvaluacion> {
  return request("/evaluacion/consentimiento", undefined, isConsentimiento, { signal, method: "GET" });
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

export interface EstadoAprendizaje {
  case_id: string;
  activo: boolean;
  version: string;
  texto: string;
  otorgado_el: string | null;
  candidatos_pendientes: number;
}

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

export type EstadoCandidato = "pendiente" | "aceptado" | "rechazado";

export interface CategoriaInfo {
  clave: string;
  descripcion: string;
  critica: boolean;
}

export interface GrupoResumen {
  id: string;
  grupo: string;
  pregunta: string;
  tipo_fallo: string;
  etiqueta_fallo: string;
  categoria_sugerida: string;
  critica: boolean;
  origen: "valoracion" | "voto" | "senal";
  frecuencia: number;
  prioridad: number;
  motivos_prioridad: string[];
  importe_en_juego: string | null;
  creado_el: string;
  estado: EstadoCandidato;
}

export interface ColaRevision {
  grupos: GrupoResumen[];
  operador: boolean;
  categorias: CategoriaInfo[];
  detectados: number;
}

export interface CambioRedaccion {
  campo: string;
  tipo: string;
  sustituto: string;
  vista: string;
}

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

export interface ContenidoCandidato {
  pregunta: string;
  respuesta: string | null;
  respuesta_parcial: boolean;
  siguiente_pregunta: string | null;
  correccion: Record<string, string | null>;
  modo: string | null;
  cerebro: CerebroCandidato | null;
  // Only the summary the audit trail kept, when there is no full report.
  cerebro_resumen?: { ruta?: string; tipo?: string; especialistas?: string[]; violaciones_cifras?: number } | null;
  modos_especialistas: Record<string, string | null>;
  variante: string | null;
  modelos: Record<string, string>;
  modelos_origen: "turno" | "configuracion";
  senales: Array<{ tipo: string; detalle: string }>;
  tool_trace: Array<{ tool: string; agent?: string; is_error?: boolean }>;
  instantanea: { hechos?: Record<string, unknown> };
  enmascarado: CambioRedaccion[];
}

export interface Candidato {
  id: string;
  grupo: string;
  origen: "valoracion" | "voto" | "senal";
  tipo_fallo: string;
  categoria_sugerida: string;
  critica: boolean;
  importe_en_juego: string | null;
  prioridad: number;
  motivos_prioridad: string[];
  estado: EstadoCandidato;
  creado_el: string;
  motivo_rechazo: string | null;
  caso_aceptado: string | null;
  contenido: ContenidoCandidato;
}

export interface DetalleCandidato {
  candidato: Candidato;
  etiqueta_fallo: string;
  frecuencia: number;
  duplicados: string[];
  categorias: CategoriaInfo[];
}

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

export interface Aceptado {
  id: string;
  categoria: string;
  descripcion: string;
  expectativas: Array<Record<string, unknown>>;
  avisos: string[];
  aceptado_el: string;
  vigente: boolean;
  motivo_baja: string | null;
}

function isCola(value: unknown): value is ColaRevision {
  return isRecord(value) && Array.isArray(value.grupos) && Array.isArray(value.categorias);
}

function isDetalle(value: unknown): value is DetalleCandidato {
  return isRecord(value) && isRecord(value.candidato) && Array.isArray(value.categorias);
}

function isAceptado(value: unknown): value is Aceptado {
  return isRecord(value) && typeof value.id === "string" && Array.isArray(value.expectativas);
}

function isAceptados(value: unknown): value is { casos: Aceptado[] } {
  return isRecord(value) && Array.isArray(value.casos);
}

function isRechazo(value: unknown): value is { rechazados: number } {
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
