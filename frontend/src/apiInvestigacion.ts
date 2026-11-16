// Client for «Investigar» (hacienda_gpt/api/routers/investigacion.py): research with sources.
//
// Shapes mirror hacienda_gpt/capacidades/investigacion/modelo.py (PeticionInvestigacion, Informe). Money comes
// as decimal strings. Everything goes through api.ts's `request`: same session token, timeout and errors.

import { request } from "./api";

export type TipoFuente = "corpus" | "boe" | "web";
export type Profundidad = "rapida" | "normal" | "a_fondo";
export type Confianza = "alta" | "media" | "baja";

export interface Cita {
  fuente: string;
  cita: string;
}

export interface Afirmacion {
  texto: string;
  tema: string;
  citas: Cita[];
  oficial: boolean;
}

export interface FuenteInforme {
  id: string;
  tipo: TipoFuente;
  titulo: string;
  url: string | null;
  referencia: string | null;
  dominio: string | null;
  oficial: boolean;
  recuperada_el: string;
  citada: boolean;
  sospechosa: boolean;
  nota: string | null;
}

export interface Conflicto {
  tema: string;
  fuentes: string[];
  descripcion: string;
  origen: "cifras" | "modelo";
}

export interface Informe {
  pregunta: string;
  ambito: string | null;
  profundidad: Profundidad;
  fuentes_pedidas: TipoFuente[];
  respuesta: string;
  afirmaciones: Afirmacion[];
  fuentes: FuenteInforme[];
  conflictos: Conflicto[];
  confianza: Confianza;
  no_verificado: string[];
  avisos: string[];
  web: {
    pedida: boolean;
    usada: boolean;
    motivo: string | null;
    solo_oficiales: boolean;
    busquedas: number;
    paginas_leidas: number;
    vistas_sin_leer: string[];
  };
  coste: {
    llamadas_modelo: number;
    tokens_entrada: number;
    tokens_salida: number;
    busquedas_web: number;
    usd: string | null;
    limite_alcanzado: string | null;
  };
  generado_el: string;
  desde_cache: boolean;
}

export interface EstadoInvestigacion {
  fuentes: Record<TipoFuente, boolean>;
  web_activada_por_el_servicio: boolean;
  web_consentida: boolean;
  web_proveedor_compatible: boolean;
  web_motivo: string | null;
  dominios_permitidos: string[];
  dominios_bloqueados: string[];
  limites: {
    max_usd: string;
    max_tokens: number;
    llamadas_por_profundidad: Record<string, number>;
    precio_busqueda_web_usd: string;
    precio_busqueda_web_fuente: string;
  };
  privacidad: string;
  version_texto_consentimiento: string;
}

export interface PeticionInvestigacion {
  pregunta: string;
  fuentes: TipoFuente[];
  profundidad: Profundidad;
  solo_oficiales: boolean;
  case_id?: string;
}

const esObjeto = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null;

function esEstado(v: unknown): v is EstadoInvestigacion {
  return esObjeto(v) && esObjeto(v.fuentes) && typeof v.web_consentida === "boolean" && typeof v.privacidad === "string";
}

function esInforme(v: unknown): v is Informe {
  return (
    esObjeto(v) &&
    typeof v.respuesta === "string" &&
    Array.isArray(v.afirmaciones) &&
    Array.isArray(v.fuentes) &&
    esObjeto(v.coste) &&
    esObjeto(v.web)
  );
}

// A research makes several model calls and may read web pages: give it time.
const TIEMPO_INVESTIGACION_MS = 180_000;

export function getEstadoInvestigacion(signal?: AbortSignal): Promise<EstadoInvestigacion> {
  return request("/investigacion/estado", undefined, esEstado, { method: "GET", signal });
}

export function cambiarConsentimientoWeb(concedido: boolean): Promise<EstadoInvestigacion> {
  return request("/investigacion/consentimiento", { concedido }, esEstado);
}

export function investigar(peticion: PeticionInvestigacion, signal?: AbortSignal): Promise<Informe> {
  return request("/investigacion", peticion, esInforme, { signal, timeoutMs: TIEMPO_INVESTIGACION_MS });
}
