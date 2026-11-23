// Client for the self-employed calculators (POST /autonomos/*) and for the
// activity as the case sees it (POST /simulate). Deterministic endpoints: no
// model call, so they answer as the user types.
//
// Everything goes through api.ts's `request`, so these calls carry the session
// token, the timeout and the error messages of the rest of the app.

import { request } from "./api";
import { isRecord } from "./lib/guards";
import type { Esquemas, Peticion, Respuesta } from "./api/tipos";

// The shapes come from the backend's OpenAPI schema (src/api/esquema.d.ts). Amounts are decimals as strings.
export type ActividadInput = Peticion<"/autonomos/actividad", "post">;
export type ResultadoActividad = Respuesta<"/autonomos/actividad", "post">;
export type CeutaMelilla = NonNullable<ActividadInput["ceuta_melilla"]>;
export type Modelo130Input = Peticion<"/autonomos/modelo-130", "post">;
// `obligado_a_pago_fraccionado`: null when it cannot be told from the figures given.
export type ResultadoModelo130 = Respuesta<"/autonomos/modelo-130", "post">;
// The schema says an integer from 1 to 4; the UI picks one of the four.
export type Trimestre = 1 | 2 | 3 | 4;
export type Modelo303Input = Peticion<"/autonomos/modelo-303", "post">;
// `resultado` positive: to pay; negative: balance in the taxpayer's favour.
export type ResultadoModelo303 = Respuesta<"/autonomos/modelo-303", "post">;
/** What the profile bridge did with the activity figures of a case (see asistente/ambitos/fiscal/profile.py). */
export type ActividadPerfil = Esquemas["ActividadPerfil"];

function isResultado(value: unknown, ...amounts: string[]): boolean {
  return (
    isRecord(value) &&
    typeof value.ejercicio === "number" &&
    Array.isArray(value.lineas) &&
    Array.isArray(value.avisos) &&
    typeof value.fuente === "string" &&
    amounts.every((name) => typeof value[name] === "string")
  );
}

function isActividad(value: unknown): value is ResultadoActividad {
  return isResultado(value, "rendimiento_actividad_neto", "rendimiento_neto");
}
function isModelo130(value: unknown): value is ResultadoModelo130 {
  return isResultado(value, "resultado", "a_ingresar", "pago_previo_a_minoracion");
}
function isModelo303(value: unknown): value is ResultadoModelo303 {
  return isResultado(value, "resultado", "saldo_a_compensar", "tipo_resultado");
}
function isEjercicios(value: unknown): value is Respuesta<"/autonomos/ejercicios", "get"> {
  return isRecord(value) && Array.isArray(value.ejercicios) && value.ejercicios.every((year) => typeof year === "number");
}
function isActividadPerfil(value: unknown): value is ActividadPerfil {
  return isRecord(value) && typeof value.calculado === "boolean" && typeof value.aviso === "string" && (value.resultado === null || isActividad(value.resultado));
}
interface SimulacionConActividad {
  escenarios: Array<{ actividad?: ActividadPerfil | null }>;
}
function isSimulacionConActividad(value: unknown): value is SimulacionConActividad {
  return (
    isRecord(value) &&
    Array.isArray(value.escenarios) &&
    value.escenarios.length > 0 &&
    value.escenarios.every((item) => isRecord(item) && (item.actividad === undefined || item.actividad === null || isActividadPerfil(item.actividad)))
  );
}

/** Years the calculators have parameters for, ascending. */
export async function getEjerciciosAutonomos(signal?: AbortSignal): Promise<number[]> {
  const data = await request("/autonomos/ejercicios", undefined, isEjercicios, { signal, method: "GET" });
  return data.ejercicios;
}

/** Annual net income of the activity (estimación directa simplificada), from the figures alone. */
export function calcularActividad(input: ActividadInput, signal?: AbortSignal): Promise<ResultadoActividad> {
  return request("/autonomos/actividad", input, isActividad, { signal });
}

/**
 * The same computation as the case sees it: `cambios` (actividad_* facts) layered over the saved
 * facts, with the case's other income deciding the art. 32.2 reductions. Null when the case and
 * the changes hold no activity figures.
 */
export async function calcularActividadDelExpediente(opts: {
  caseId: string;
  ejercicio: number;
  cambios: Record<string, unknown>;
  signal?: AbortSignal;
}): Promise<ActividadPerfil | null> {
  const body = { case_id: opts.caseId, ejercicio: opts.ejercicio, datos: {}, escenarios: [{ nombre: "Actividad", cambios: opts.cambios }] };
  const data = await request("/simulate", body, isSimulacionConActividad, { signal: opts.signal });
  return data.escenarios[0]?.actividad ?? null;
}

/** Quarterly IRPF payment on account (modelo 130), from cumulative figures. */
export function calcularModelo130(input: Modelo130Input, signal?: AbortSignal): Promise<ResultadoModelo130> {
  return request("/autonomos/modelo-130", input, isModelo130, { signal });
}

/** Quarterly VAT return (modelo 303), general regime. */
export function calcularModelo303(input: Modelo303Input, signal?: AbortSignal): Promise<ResultadoModelo303> {
  return request("/autonomos/modelo-303", input, isModelo303, { signal });
}
