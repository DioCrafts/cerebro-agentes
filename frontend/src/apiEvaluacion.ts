// Client for the feedback on the assistant's answers (hacienda_gpt/api/routers/evaluacion.py).
//
// A vote alone never stores the conversation. Storing it as an evaluation case needs the user to tick the
// opt-in box for THAT answer; the request then carries the version of the consent text they saw.
// Everything goes through api.ts's `request`: same session token, timeout and error messages.

import { request } from "./api";

export type TipoCorreccion = "cifra_erronea" | "modulo_erroneo" | "hecho_omitido" | "afirmacion_inventada" | "otro";

export interface ConsentimientoEvaluacion {
  version: string;
  texto: string;
  tipos: Array<{ clave: TipoCorreccion; etiqueta: string }>;
}

export interface FeedbackRespuesta {
  id: string;
  guardado_para_evaluacion: boolean;
  estado: string;
  etiquetas: string[];
  mensaje: string;
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

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
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
