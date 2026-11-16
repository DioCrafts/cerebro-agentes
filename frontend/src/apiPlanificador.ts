// Client for the deterministic household planner (POST /planificar): the best use of a sum of
// money among early mortgage repayment, pension contributions, a donation and liquidity. No model call.
//
// Shapes mirror hacienda_gpt/planificador/modelos.py (EntradaPlan, PlanHogar). Amounts are decimals
// sent back as strings ("1082.46"). Everything goes through api.ts's `request`: session, timeout, errors.

import { request } from "./api";
import type { PlanOptimo, TextoLegal } from "./apiOptimizador";

export type PalancaId = "amortizar_hipoteca" | "plan_pensiones_contribuyente" | "plan_pensiones_conyuge" | "donativo";
export type Modalidad = "plazo" | "cuota";
export type VistaPensiones = "diferimiento" | "definitivo";

export interface RestriccionesPlan {
  excluidas?: PalancaId[];
  modalidades_excluidas?: Modalidad[];
  maximos?: Partial<Record<PalancaId, number>>;
  motivos?: Partial<Record<PalancaId, string>>;
  origen?: string | null;
}

export interface PlanificarRequest {
  case_id?: string;
  ejercicio?: number;
  importe_disponible?: number;
  excedente_mensual?: number;
  horizonte_anos?: number;
  rentabilidad_alternativa_pct?: number;
  colchon_objetivo?: number | null;
  gastos_mensuales?: number | null;
  meses_colchon?: number;
  liquidez_actual?: number;
  penalizacion_liquidez?: number | null;
  tipo_rescate_pct?: number | null;
  valorar_pensiones?: VistaPensiones | null;
  donativo_previsto?: number;
  presupuesto_donativos?: number | null;
  parte_hipoteca_conyuge?: number;
  proteger_ayudas?: boolean;
  restricciones?: RestriccionesPlan;
}

export interface Asignacion {
  amortizar_hipoteca: string;
  modalidad: Modalidad | null;
  plan_pensiones_contribuyente: string;
  plan_pensiones_conyuge: string;
  donativo: string;
}

export interface Componente {
  concepto: string;
  importe: string;
  naturaleza: "definitivo" | "diferimiento" | "coste" | "rendimiento" | "penalizacion";
  palanca: string | null;
  modulo: string;
  referencia: string | null;
}

export interface Valoracion {
  asignacion: Asignacion;
  usado: string;
  liquido: string;
  falta_colchon: string;
  componentes: Componente[];
  total: string;
  total_diferimiento: string;
  total_definitivo: string;
  valor_actual: string;
  impuesto_ejercicio: string;
  tributacion: string;
  cuota_liquida: string;
  base_imponible: string;
}

export interface LineaAsignacion {
  id: string;
  nombre: string;
  importe: string;
  efectivo: string;
  detalle: string;
}

export interface PalancaPlan {
  id: string;
  nombre: string;
  importe: string;
  valor_marginal: string;
  valor_aislado: string;
  importe_aislado: string;
  naturaleza: string;
  explicacion: string;
  referencias: string[];
}

export interface PalancaNoUsada {
  id: string;
  nombre: string;
  causa: "restricciones" | "faltan_datos" | "sin_margen" | "fecha" | "no_compensa" | "modulo_inactivo";
  detalle: string;
}

export interface EfectoAyuda {
  id: string;
  nombre: string;
  magnitud: string;
  cita: string;
  cambio: string | null;
  estado_antes: string;
  estado_despues: string;
  importe_antes: string | null;
  importe_despues: string | null;
  explicacion: string;
}

export interface ResumenHipoteca {
  capital_pendiente: string;
  tipo_actual: string;
  cuota_actual: string;
  fin_actual: string;
  importe: string;
  modalidad: Modalidad | null;
  comision: string;
  comision_porcentaje: string;
  comision_es_maximo: boolean;
  cuota_nueva: string | null;
  fin_nuevo: string | null;
  intereses_ahorrados_horizonte: string;
  intereses_ahorrados_total: string;
  fundamento_comision: string[];
}

export interface Escenario {
  nombre: string;
  asignacion: Asignacion;
  total: string;
  factible: boolean;
  nota: string;
}

export interface BusquedaPlan {
  metodo: "exhaustiva";
  resolucion: string;
  resolucion_hipoteca: string | null;
  combinaciones: number;
  recalculadas: number;
  evaluaciones_irpf: number;
  candidatos: Record<string, number>;
  nota: string;
}

export interface PlanHogar {
  ejercicio: number;
  fecha: string;
  fecha_ejecucion: string;
  fecha_horizonte: string;
  horizonte_anos: number;
  rentabilidad_alternativa_pct: string;
  vista: VistaPensiones;
  tipo_rescate_pct: string | null;
  importe_total: string;
  colchon_objetivo: string | null;
  liquidez_antes: string;
  liquidez_despues: string;
  asignacion: LineaAsignacion[];
  valoracion: Valoracion;
  palancas: PalancaPlan[];
  no_usadas: PalancaNoUsada[];
  suma_aislados: string;
  interaccion: string;
  explicacion_interaccion: string[];
  escenarios: Escenario[];
  hipoteca: ResumenHipoteca | null;
  ayudas: EfectoAyuda[];
  plan_fiscal: PlanOptimo | null;
  restricciones: RestriccionesPlan;
  supuestos: string[];
  avisos: string[];
  no_modelado: string[];
  citas: string[];
  textos_legales: TextoLegal[];
  busqueda: BusquedaPlan;
  explicacion: string;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

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
