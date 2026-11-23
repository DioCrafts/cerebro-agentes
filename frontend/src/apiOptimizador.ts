// Client for the deterministic tax optimiser (POST /optimize): the best
// combination of the choices the law leaves to a household. No model call.
//
// The shapes come from the backend's OpenAPI schema (src/api/esquema.d.ts). Amounts are decimals sent as strings ("1082.46").
// Everything goes through api.ts's `request`: session token, timeout, errors.

import { request } from "./api";
import { isRecord } from "./lib/guards";
import type { Esquemas, Peticion, Respuesta } from "./api/tipos";

export type OptimizeRequest = Peticion<"/optimize", "post">;
// With a case, every field NOT sent is taken from the preferences the case keeps (`preferencias_optimizacion`);
// `null` says «not stated» and overrides a stored value.
export type Restricciones = Esquemas["Restricciones"];
// `id` is the screen's row id; kept with the stored preferences, ignored by the optimiser.
export type PosicionLatente = Esquemas["PosicionLatente"];
export type TipoActivo = NonNullable<PosicionLatente["tipo"]>;
export type Titular = NonNullable<PosicionLatente["titular"]>;
export type PlanOptimo = Respuesta<"/optimize", "post">;
// `resultado_total`: positive, to pay; negative, refund.
export type ResultadoHogar = PlanOptimo["base"];
export type Tributacion = ResultadoHogar["tributacion"];
export type Declaracion = ResultadoHogar["declaraciones"][number];
export type ResultadoDeclaracion = Declaracion["resultado"];
// `compromiso`: euros the lever locks, spends or realises (0 for a filing option); `ahorro_marginal`: tax the plan
// would pay more without it; `ahorro_aislado`: saving of the lever alone, at its own best value (can be negative).
export type PalancaElegida = PlanOptimo["palancas"][number];
export type Naturaleza = PalancaElegida["naturaleza"];
export type PalancaNoDisponible = PlanOptimo["no_disponibles"][number];
export type CausaNoDisponible = PalancaNoDisponible["causa"];
export type PalancaDescartada = PlanOptimo["descartadas"][number];
export type CambioLinea = PlanOptimo["cambios_traza"][number];
export type TextoLegal = PlanOptimo["textos_legales"][number];
export type Busqueda = PlanOptimo["busqueda"];

const isStrings = (value: unknown): value is string[] => Array.isArray(value) && value.every((item) => typeof item === "string");

function isHogar(value: unknown): value is ResultadoHogar {
  return (
    isRecord(value) &&
    typeof value.tributacion === "string" &&
    typeof value.resultado_total === "string" &&
    Array.isArray(value.declaraciones) &&
    value.declaraciones.every((d) => isRecord(d) && typeof d.titular === "string" && isRecord(d.resultado) && Array.isArray(d.resultado.lineas))
  );
}

function isPalanca(value: unknown): value is PalancaElegida {
  return (
    isRecord(value) &&
    typeof value.id === "string" &&
    typeof value.accion === "string" &&
    typeof value.naturaleza === "string" &&
    typeof value.ahorro_marginal === "string" &&
    typeof value.ahorro_aislado === "string" &&
    typeof value.verificado === "boolean" &&
    isStrings(value.requisitos)
  );
}

function isPlan(value: unknown): value is PlanOptimo {
  return (
    isRecord(value) &&
    typeof value.ejercicio === "number" &&
    typeof value.fase === "string" &&
    typeof value.ahorro_total === "string" &&
    typeof value.interaccion === "string" &&
    isHogar(value.base) &&
    isHogar(value.optimo) &&
    Array.isArray(value.palancas) &&
    value.palancas.every(isPalanca) &&
    Array.isArray(value.no_disponibles) &&
    Array.isArray(value.descartadas) &&
    Array.isArray(value.cambios_traza) &&
    Array.isArray(value.textos_legales) &&
    isStrings(value.explicacion_interaccion) &&
    isStrings(value.avisos) &&
    isStrings(value.supuestos) &&
    isStrings(value.datos_que_faltan) &&
    isStrings(value.oportunidades) &&
    isStrings(value.citas) &&
    isRecord(value.busqueda) &&
    typeof value.explicacion === "string"
  );
}

/** The optimal plan for the household. A household with many levers can take a second or two. */
export function optimizar(body: OptimizeRequest, signal?: AbortSignal): Promise<PlanOptimo> {
  return request("/optimize", body, isPlan, { signal, timeoutMs: 60_000 });
}
