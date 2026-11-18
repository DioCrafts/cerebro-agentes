// Client for the household council (asistente/plataforma/api/routers/consejo.py): hard decisions studied by several
// AIs that work on their own, critique each other anonymously and rule — always after the user confirms the cost.
//
// Everything goes through api.ts's `request`: same session token, timeout and error messages.

import { useEffect, useState } from "react";

import { request } from "./api";
import { errorMessage } from "./lib/memoria";

export type EstadoSesion =
  | "pendiente_confirmacion"
  | "en_cola"
  | "instruyendo"
  | "deliberando"
  | "sintetizando"
  | "terminada"
  | "fallida"
  | "cancelada"
  | "rechazada";

export type Consenso = "unanime" | "mayoria" | "dividido" | "insuficiente";

export interface PasoProgreso {
  tipo: "fase" | "miembro" | "aviso";
  fase: string | null;
  estado: string;
  miembro_id: string | null;
  en: string;
}

export interface CifraConsejo {
  id: string;
  especialista: string;
  concepto: string;
  valor: string;
  unidad?: string | null;
  fragment_id?: string | null;
  herramienta?: string | null;
  ejercicio?: number | null;
  literal: string;
}

export interface OpcionConsejo {
  id: string;
  descripcion: string;
}

export interface HechoExpediente {
  clave: string;
  texto: string;
  origen: "expediente" | "especialista" | "memoria" | "decision_usuario" | "usuario";
  referencia?: string | null;
}

export interface ExpedienteConsejo {
  id: string;
  pregunta: string;
  opciones: OpcionConsejo[];
  hechos: HechoExpediente[];
  cifras: CifraConsejo[];
  fragment_ids: string[];
  perfil_minimo: Record<string, string>;
  restricciones: string[];
  incertidumbres: string[];
  preferencia_usuario?: string | null;
  anonimizado: string[];
}

export interface PropuestaConsejo {
  miembro_id: string;
  ronda: 1 | 2;
  recomendacion: string;
  condicion?: string | null;
  confianza: number;
  razonamiento: string;
  supuestos: string[];
  riesgos: string[];
  que_me_haria_cambiar: string[];
  cifras_usadas: string[];
  cambios?: string | null;
}

export interface CriticaConsejo {
  evaluador_id: string;
  etiqueta: string;
  fortalezas: string[];
  debilidades: string[];
  errores: { tipo: string; texto: string }[];
  puntuacion: { rigor: number; completitud: number; prudencia: number; claridad: number; ajuste_al_caso: number };
}

export interface RankingConsejo {
  evaluador_id: string;
  orden: string[];
}

export interface RolConsejo {
  id: string;
  nombre: string;
  mandato: string;
  proposito: string;
  familia_preferida?: string | null;
}

export interface MiembroConsejo {
  rol: RolConsejo;
  proveedor: string;
  modelo: string;
}

export interface PosturaOpcion {
  opcion_id: string;
  a_favor: string[];
  en_contra: string[];
  miembros: string[];
}

export interface AtribucionCifraConsejo {
  literal: string;
  valor: string;
  cifra_id?: string | null;
  especialista?: string | null;
  fragment_id?: string | null;
  del_usuario: boolean;
}

export interface Dictamen {
  sesion_id: string;
  recomendacion: string;
  condicion?: string | null;
  confianza: number;
  consenso: Consenso;
  acuerdo?: number | null;
  resumen: string;
  posturas: PosturaOpcion[];
  disenso?: string | null;
  supuestos_decisivos: string[];
  sensibilidad: string[];
  datos_que_resolverian: string[];
  puerta?: { violaciones: { tipo: string; literal: string; detalle: string }[]; atribuciones: AtribucionCifraConsejo[] } | null;
  miembros: MiembroConsejo[];
  autoria: Record<string, string>;
  avisos: string[];
}

export interface CosteSesion {
  llamadas: number;
  usd: string;
  tokens_entrada: number;
  tokens_salida: number;
}

export interface ResumenSesion {
  id: string;
  estado: EstadoSesion;
  fase: string | null;
  pregunta_usuario: string;
  creada: string;
  actualizada: string;
  estimacion_usd: string;
  tope_usd: string;
  coste_usd: string;
  minutos_estimados: number;
  origen: string;
  recomendacion: string | null;
  consenso: string | null;
  confianza: number | null;
  notificacion_id: string | null;
  error: string | null;
}

export interface SesionConsejo extends ResumenSesion {
  miembros: string[];
  motivo: string;
  opciones_sugeridas: string[];
  precios_conocidos: boolean;
  llamadas_estimadas: number;
  desglose_usd: Record<string, string>;
  progreso: PasoProgreso[];
  coste: CosteSesion;
  expediente: ExpedienteConsejo | null;
  propuestas: PropuestaConsejo[];
  criticas: CriticaConsejo[];
  rankings: RankingConsejo[];
  dictamen: Dictamen | null;
  autoria_revelada: boolean;
  confirmada_ahora?: boolean | null;
}

export interface ListaSesiones {
  case_id: string;
  disponible: boolean;
  sesiones: ResumenSesion[];
}

export interface Deteccion {
  dificil: boolean;
  motivos: string[];
  opciones_detectadas: string[];
  puntuacion: number;
  pedido_por_usuario: boolean;
  factual: boolean;
}

/** States in which the council is still working (the UI polls them). */
export const EN_MARCHA: ReadonlySet<EstadoSesion> = new Set(["en_cola", "instruyendo", "deliberando", "sintetizando"]);

export const ESTADO_LABEL: Record<EstadoSesion, string> = {
  pendiente_confirmacion: "Esperando tu confirmación",
  en_cola: "En cola",
  instruyendo: "Preparando el expediente",
  deliberando: "Deliberando",
  sintetizando: "Redactando el dictamen",
  terminada: "Dictamen listo",
  fallida: "No pudo terminar",
  cancelada: "Cancelada",
  rechazada: "Descartada",
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function isSesion(value: unknown): value is SesionConsejo {
  return isRecord(value) && typeof value.id === "string" && typeof value.estado === "string" && Array.isArray(value.progreso);
}

function isLista(value: unknown): value is ListaSesiones {
  return isRecord(value) && typeof value.disponible === "boolean" && Array.isArray(value.sesiones);
}

function isDeteccion(value: unknown): value is Deteccion {
  return isRecord(value) && typeof value.dificil === "boolean" && Array.isArray(value.motivos);
}

function base(caseId: string): string {
  return `/cases/${encodeURIComponent(caseId)}/consejo`;
}

export function listarSesiones(caseId: string, signal?: AbortSignal): Promise<ListaSesiones> {
  return request(base(caseId), undefined, isLista, { signal, method: "GET" });
}

export function obtenerSesion(caseId: string, sesionId: string, signal?: AbortSignal): Promise<SesionConsejo> {
  return request(`${base(caseId)}/${encodeURIComponent(sesionId)}`, undefined, isSesion, { signal, method: "GET" });
}

export function convocarConsejo(
  caseId: string,
  datos: { pregunta: string; opciones?: string[]; preferencia?: string | null },
): Promise<SesionConsejo> {
  return request(base(caseId), datos, isSesion);
}

export function detectarDecision(caseId: string, pregunta: string, signal?: AbortSignal): Promise<Deteccion> {
  return request(`${base(caseId)}/detectar`, { pregunta }, isDeteccion, { signal });
}

function accion(caseId: string, sesionId: string, que: "confirmar" | "rechazar" | "cancelar"): Promise<SesionConsejo> {
  return request(`${base(caseId)}/${encodeURIComponent(sesionId)}/${que}`, {}, isSesion);
}

export const confirmarSesion = (caseId: string, sesionId: string) => accion(caseId, sesionId, "confirmar");
export const rechazarSesion = (caseId: string, sesionId: string) => accion(caseId, sesionId, "rechazar");
export const cancelarSesion = (caseId: string, sesionId: string) => accion(caseId, sesionId, "cancelar");

export function borrarSesion(caseId: string, sesionId: string): Promise<null> {
  return request(`${base(caseId)}/${encodeURIComponent(sesionId)}`, undefined, (v): v is null => v === null, {
    method: "DELETE",
  });
}

/** «0,40 USD». */
export function usd(valor: string | number): string {
  const n = typeof valor === "number" ? valor : Number(valor);
  return Number.isFinite(n) ? `${n.toLocaleString("es-ES", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} USD` : String(valor);
}

/** The council's confidence in plain words. */
export function confianzaEnPalabras(confianza: number): string {
  if (confianza >= 0.75) return "Confianza alta";
  if (confianza >= 0.5) return "Confianza media";
  if (confianza >= 0.3) return "Confianza baja";
  return "Confianza muy baja";
}

export const CONSENSO_LABEL: Record<Consenso, string> = {
  unanime: "Unanimidad",
  mayoria: "Mayoría",
  dividido: "Consejo dividido",
  insuficiente: "Sin base suficiente",
};

/** A session loaded by id, polled while the council works. */
export function useSesionConsejo(caseId: string | null, sesionId: string | null): { sesion: SesionConsejo | null; error: string | null; recargar: () => void } {
  const [sesion, setSesion] = useState<SesionConsejo | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  useEffect(() => {
    if (!caseId || !sesionId) return;
    const controller = new AbortController();
    let temporizador: ReturnType<typeof setTimeout> | undefined;
    const cargar = () => {
      obtenerSesion(caseId, sesionId, controller.signal).then(
        (s) => {
          setSesion(s);
          setError(null);
          if (EN_MARCHA.has(s.estado)) temporizador = setTimeout(cargar, 4000);
        },
        (err: unknown) => {
          if (!controller.signal.aborted) setError(errorMessage(err));
        },
      );
    };
    cargar();
    return () => {
      controller.abort();
      if (temporizador) clearTimeout(temporizador);
    };
  }, [caseId, sesionId, tick]);
  return { sesion, error, recargar: () => setTick((n) => n + 1) };
}
