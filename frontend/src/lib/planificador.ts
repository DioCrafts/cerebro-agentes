// Pure helpers of the «Planificar el hogar» screen: the request built from the form, labels and
// error wording. No React here, so the component file only exports components.

import type { Componente, PalancaId, PalancaNoUsada, PlanificarRequest, VistaPensiones } from "../apiPlanificador";
import { parseAmount } from "./formulario";

export const PALANCAS: Array<{ id: PalancaId; label: string }> = [
  { id: "amortizar_hipoteca", label: "Amortizar la hipoteca" },
  { id: "plan_pensiones_contribuyente", label: "Mi plan de pensiones" },
  { id: "plan_pensiones_conyuge", label: "El plan de mi cónyuge" },
  { id: "donativo", label: "Donativo" },
];

export const HORIZONTES = [1, 3, 5, 10, 15, 20];

export interface FormularioPlan {
  importe: string;
  excedente: string;
  horizonte: number;
  rentabilidad: string;
  colchonModo: "importe" | "meses" | "ninguno";
  colchon: string;
  gastos: string;
  meses: string;
  liquidez: string;
  rescate: string;
  vista: VistaPensiones | "auto";
  donativo: string;
  presupuesto: string;
  parteConyuge: string;
  excluidas: PalancaId[];
  protegerAyudas: boolean;
}

export const FORMULARIO_INICIAL: FormularioPlan = {
  importe: "",
  excedente: "",
  horizonte: 5,
  rentabilidad: "",
  colchonModo: "meses",
  colchon: "",
  gastos: "",
  meses: "6",
  liquidez: "",
  rescate: "",
  vista: "auto",
  donativo: "",
  presupuesto: "",
  parteConyuge: "",
  excluidas: [],
  protegerAyudas: true,
};

export type Problemas = Partial<Record<keyof FormularioPlan, string>>;

/** Problems of the form, by field (empty when it can be sent). */
export function problemasDe(f: FormularioPlan): Problemas {
  const out: Problemas = {};
  const dinero = (campo: keyof FormularioPlan, valor: string, obligatorio = false) => {
    const n = parseAmount(valor);
    if (valor.trim() === "") {
      if (obligatorio) out[campo] = "Indica una cantidad.";
      return;
    }
    if (n === null || n < 0) out[campo] = "Escribe una cantidad en euros (0 o más).";
  };
  dinero("importe", f.importe);
  dinero("excedente", f.excedente);
  if ((parseAmount(f.importe) ?? 0) <= 0 && (parseAmount(f.excedente) ?? 0) <= 0 && !out.importe && !out.excedente) {
    out.importe = "Indica el dinero que puedes destinar ahora o un excedente mensual.";
  }
  const pct = (campo: keyof FormularioPlan, valor: string, min: number, max: number) => {
    if (valor.trim() === "") return;
    const n = parseAmount(valor);
    if (n === null || n < min || n > max) out[campo] = `Escribe un porcentaje entre ${min} y ${max}.`;
  };
  pct("rentabilidad", f.rentabilidad, -20, 30);
  pct("rescate", f.rescate, 0, 60);
  pct("parteConyuge", f.parteConyuge, 0, 100);
  if (f.colchonModo === "importe") dinero("colchon", f.colchon, true);
  if (f.colchonModo === "meses") {
    dinero("gastos", f.gastos, true);
    const m = Number(f.meses);
    if (!Number.isInteger(m) || m < 0 || m > 36) out.meses = "Entre 0 y 36 meses.";
  }
  dinero("liquidez", f.liquidez);
  dinero("donativo", f.donativo);
  dinero("presupuesto", f.presupuesto);
  const d = parseAmount(f.donativo) ?? 0;
  const p = parseAmount(f.presupuesto);
  if (p !== null && p < d && !out.presupuesto) out.presupuesto = "No puede ser menor que el donativo previsto.";
  if (f.vista === "definitivo" && f.rescate.trim() === "") out.rescate = "Para la vista definitiva indica el tipo al rescatar.";
  return out;
}

/** The request for POST /planificar (only what the user stated: the server says what it assumed). */
export function peticion(f: FormularioPlan, caseId: string | null | undefined, ejercicio: number | undefined): PlanificarRequest {
  const num = (s: string) => parseAmount(s) ?? undefined;
  const body: PlanificarRequest = {
    importe_disponible: num(f.importe) ?? 0,
    horizonte_anos: f.horizonte,
    proteger_ayudas: f.protegerAyudas,
  };
  if (caseId) body.case_id = caseId;
  if (ejercicio) body.ejercicio = ejercicio;
  if (num(f.excedente)) body.excedente_mensual = num(f.excedente);
  if (f.rentabilidad.trim() !== "") body.rentabilidad_alternativa_pct = num(f.rentabilidad);
  if (f.colchonModo === "importe") body.colchon_objetivo = num(f.colchon);
  if (f.colchonModo === "meses") {
    body.gastos_mensuales = num(f.gastos);
    body.meses_colchon = Number(f.meses);
  }
  if (num(f.liquidez)) body.liquidez_actual = num(f.liquidez);
  if (f.rescate.trim() !== "") body.tipo_rescate_pct = num(f.rescate);
  if (f.vista !== "auto") body.valorar_pensiones = f.vista;
  if (num(f.donativo)) body.donativo_previsto = num(f.donativo);
  if (f.presupuesto.trim() !== "") body.presupuesto_donativos = num(f.presupuesto);
  if (f.parteConyuge.trim() !== "") body.parte_hipoteca_conyuge = (num(f.parteConyuge) ?? 0) / 100;
  if (f.excluidas.length) body.restricciones = { excluidas: [...f.excluidas], origen: "usuario" };
  return body;
}

export const NATURALEZA_COMPONENTE: Record<Componente["naturaleza"], { label: string; cls: string }> = {
  definitivo: { label: "Ahorro definitivo", cls: "border-grounding-cited-bd bg-grounding-cited-bg text-grounding-cited-fg" },
  diferimiento: { label: "Diferimiento", cls: "border-grounding-uncited-bd bg-grounding-uncited-bg text-grounding-uncited-fg" },
  coste: { label: "Coste", cls: "border-risk-high-fg/30 bg-risk-high-bg text-risk-high-fg" },
  rendimiento: { label: "Rendimiento", cls: "border-line bg-surface text-ink-soft" },
  penalizacion: { label: "Penalización", cls: "border-risk-high-fg/30 bg-risk-high-bg text-risk-high-fg" },
};

export const CAUSA_NO_USADA: Record<PalancaNoUsada["causa"], string> = {
  restricciones: "Por lo que has indicado",
  faltan_datos: "Faltan datos",
  sin_margen: "Sin margen",
  fecha: "Fuera de plazo",
  no_compensa: "No compensa",
  modulo_inactivo: "Módulo no activado",
};

export const ESTADO_AYUDA: Record<string, string> = {
  cumple: "Te corresponde",
  probable: "Probable",
  automatica: "Automática",
  proximamente: "Próximamente",
  ya_solicitada: "Ya solicitada",
  faltan_datos: "Faltan datos",
  no_cumple: "No te corresponde",
};

/** A 422/404 from /planificar as a sentence for the user. */
export function humanizePlanError(message: string, status: number | null): string {
  if (status === 404) return "No se encuentra tu expediente en tu cuenta: abre una consulta nueva o calcula sin expediente.";
  return message.replace(/\s*\((?:Input|String|Value|Field|Decimal|Extra|Unable)[^)]*\)/g, "").trim();
}
