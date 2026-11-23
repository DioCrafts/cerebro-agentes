// Client for the deterministic household planner (POST /planificar): the best use of a sum of
// money among early mortgage repayment, pension contributions, a donation and liquidity. No model call.
//
// The shapes come from the backend's OpenAPI schema (src/api/esquema.d.ts). Amounts are decimals
// sent back as strings ("1082.46"). Everything goes through api.ts's `request`: session, timeout, errors.

import { request } from "./api";
import { isRecord } from "./lib/guards";
import type { Peticion, Respuesta } from "./api/tipos";

export type PlanificarRequest = Peticion<"/planificar", "post">;
export type PlanHogar = Respuesta<"/planificar", "post">;
// Filing modes ruled out (`tributacion_excluida`) come from the user or their decisions in memory.
export type RestriccionesPlan = PlanHogar["restricciones"];
export type PalancaId = NonNullable<RestriccionesPlan["excluidas"]>[number];
export type Modalidad = NonNullable<RestriccionesPlan["modalidades_excluidas"]>[number];
export type VistaPensiones = PlanHogar["vista"];
export type Valoracion = PlanHogar["valoracion"];
export type Asignacion = Valoracion["asignacion"];
export type Componente = Valoracion["componentes"][number];
export type LineaAsignacion = PlanHogar["asignacion"][number];
export type PalancaPlan = PlanHogar["palancas"][number];
export type PalancaNoUsada = PlanHogar["no_usadas"][number];
export type EfectoAyuda = PlanHogar["ayudas"][number];
export type ResumenHipoteca = NonNullable<PlanHogar["hipoteca"]>;
export type Escenario = PlanHogar["escenarios"][number];
export type BusquedaPlan = PlanHogar["busqueda"];

function isPlanHogar(value: unknown): value is PlanHogar {
  return (
    isRecord(value) &&
    typeof value.ejercicio === "number" &&
    Array.isArray(value.asignacion) &&
    isRecord(value.valoracion) &&
    Array.isArray(value.palancas) &&
    typeof value.explicacion === "string"
  );
}

/** The household plan. A couple with a mortgage and several levers can take a couple of seconds. */
export function planificar(body: PlanificarRequest, signal?: AbortSignal): Promise<PlanHogar> {
  return request("/planificar", body, isPlanHogar, { signal, timeoutMs: 90_000 });
}
