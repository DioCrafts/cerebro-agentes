// From what is on «Tus datos» to the body of POST /optimize: the figures typed over the case, the
// spouse's figures and the constraints, with the problem of each box. Pure, so it is tested alone.
//
// The request always carries the screen's spouse figures and constraints in full, so the server's
// stored defaults never add something the screen does not show.

import type { Fact } from "../../api";
import type { OptimizeRequest, PosicionLatente, Restricciones } from "../../apiOptimizador";
import { fieldProblem, parseAmount } from "../formulario";
import {
  type BaseExpediente,
  COUNT_NAMES,
  type Iliquidez,
  type LimitName,
  MONOPARENTAL,
  PLAN_INPUT_FACTS,
  type Position,
  SPOUSE,
  type TributacionPrevista,
  USER_FIELDS,
} from "./campos";

/** What the user typed over the case (absent / null = what the case says). */
export interface FormularioOptimizador {
  edits: Record<string, string>;
  comunidadEdit: string | null;
  estadoEdit: string | null;
  extraFields: string[];
  spouseEdits: Record<string, string>;
  tributacionEdit: TributacionPrevista | null;
  limitEdits: Partial<Record<LimitName, string>>;
  iliquidezEdit: Iliquidez | null;
  positionsEdit: Position[] | null;
  fechaEdit: string | null;
}

export const FORMULARIO_VACIO: FormularioOptimizador = {
  edits: {},
  comunidadEdit: null,
  estadoEdit: null,
  extraFields: [],
  spouseEdits: {},
  tributacionEdit: null,
  limitEdits: {},
  iliquidezEdit: null,
  positionsEdit: null,
  fechaEdit: null,
};

export interface ContextoPeticion {
  facts: Fact[];
  caseId: string | null;
  /** The tax year of the case's period, or null. */
  taxYear: number | null;
  /** Today in Spain, AAAA-MM-DD. */
  today: string;
}

/** The form resolved over the case: what each box shows. */
export interface Vista {
  value: (name: string) => string;
  spouseValue: (name: string) => string;
  limitValue: (name: LimitName) => string;
  iliquidez: Iliquidez;
  positions: Position[];
  tributacionActual: TributacionPrevista;
  comunidad: string;
  estado: string;
  married: boolean;
  /** Children: the per-child detail of the case, or the typed count. */
  hijos: number;
}

export function resolver(form: FormularioOptimizador, base: BaseExpediente): Vista {
  const value = (name: string) => form.edits[name] ?? base.baseline[name] ?? "";
  const estado = form.estadoEdit ?? base.savedEstado;
  return {
    value,
    spouseValue: (name) => form.spouseEdits[name] ?? base.spouseBaseline[name] ?? "",
    limitValue: (name) => form.limitEdits[name] ?? base.limitsBaseline[name] ?? "",
    iliquidez: form.iliquidezEdit ?? base.iliquidezBaseline,
    positions: form.positionsEdit ?? base.positionsBaseline,
    tributacionActual: form.tributacionEdit ?? base.tributacionBaseline,
    comunidad: form.comunidadEdit ?? base.savedComunidad,
    estado,
    married: estado === "casado",
    hijos: base.caseChildren ?? parseAmount(value("num_descendientes")) ?? 0,
  };
}

export interface Peticion {
  /** { id of the box: what is wrong with it }. */
  problems: Record<string, string>;
  /** The user's figures that differ from the case (what the request sends in `datos`). */
  datos: Record<string, unknown>;
  /** Whether at least one figure of the spouse is on screen. */
  spouseGiven: boolean;
  restricciones: Restricciones;
  body: OptimizeRequest;
  /** What the plan depends on: a plan computed with another key is outdated. */
  key: string;
}

const CIFRA = "Escribe una cifra, por ejemplo 1.500,50.";

export function construirPeticion(form: FormularioOptimizador, base: BaseExpediente, v: Vista, ctx: ContextoPeticion): Peticion {
  const { baseline, caseChildren, savedComunidad, savedEstado } = base;
  const problems: Record<string, string> = {};
  const datos: Record<string, unknown> = {};
  for (const def of USER_FIELDS) {
    if (caseChildren !== null && COUNT_NAMES.has(def.name)) continue;
    if (def === MONOPARENTAL && (v.married || v.hijos <= 0)) continue;
    const raw = form.edits[def.name];
    const problem = fieldProblem(def.kind, v.value(def.name));
    if (problem) problems[`opt-${def.name}`] = problem;
    if (raw === undefined || problem) continue;
    if (def.kind === "flag") {
      if ((raw === "true") !== (baseline[def.name] === "true")) datos[def.name] = raw === "true";
      continue;
    }
    const n = parseAmount(raw);
    const before = parseAmount(baseline[def.name]);
    if (n === null) {
      // A saved figure cleared in the form: zero, except an age (unknown is not zero years).
      if (before !== null && def.kind !== "age") datos[def.name] = 0;
    } else if (n !== before) datos[def.name] = n;
  }
  const { comunidadEdit, estadoEdit, fechaEdit } = form;
  if (comunidadEdit !== null && comunidadEdit !== savedComunidad && comunidadEdit) {
    datos.comunidad_autonoma = comunidadEdit === "Otra" ? "otra comunidad" : comunidadEdit;
  }
  if (estadoEdit !== null && estadoEdit !== savedEstado && estadoEdit) datos.estado_civil = estadoEdit;

  // Every field of the spouse's section, null when empty: it replaces what the case keeps, field by field.
  const conyuge: Record<string, number | boolean | null> = {};
  let spouseGiven = false;
  if (v.married) {
    for (const def of SPOUSE) {
      const raw = v.spouseValue(def.name);
      conyuge[def.name] = null;
      if (def.kind === "flag") {
        if (raw === "true") conyuge[def.name] = true;
      } else {
        const problem = fieldProblem(def.kind, raw);
        if (problem) problems[`opt-conyuge-${def.name}`] = problem;
        const n = parseAmount(raw);
        if (!problem && n !== null) conyuge[def.name] = n;
      }
      if (conyuge[def.name] !== null) spouseGiven = true;
    }
  }
  // Sent either way: a stored «conjunta» must not stay the starting point when the screen says «individual».
  if (spouseGiven) datos.tributacion = v.tributacionActual;

  const limit = (name: LimitName, positive = false): number | undefined => {
    const raw = v.limitValue(name);
    if (!raw.trim()) return undefined;
    const n = parseAmount(raw);
    if (n === null) problems[`opt-${name}`] = CIFRA;
    else if (n < 0 || (positive && n === 0)) problems[`opt-${name}`] = positive ? "Debe ser mayor que cero." : "No puede ser negativo.";
    else return n;
    return undefined;
  };
  const pensionMax = limit("aportacion_maxima_pensiones");
  const pensionMaxConyuge = v.married ? limit("aportacion_maxima_pensiones_conyuge") : undefined;
  const previsto = limit("donativo_previsto");
  let presupuesto = limit("presupuesto_donativos");
  if (presupuesto !== undefined && presupuesto < (previsto ?? 0)) {
    problems["opt-presupuesto_donativos"] = "No puede ser menor que el donativo que ya vas a hacer.";
    presupuesto = undefined;
  }
  const recurrente = limit("donativo_recurrente_importe_anterior", true);
  const perdidas: PosicionLatente[] = [];
  const ganancias: PosicionLatente[] = [];
  for (const pos of v.positions) {
    const n = parseAmount(pos.importe);
    const id = `opt-pos-${pos.id}-importe`;
    if (!pos.importe.trim()) problems[id] = "Indica el importe (o quita esta fila).";
    else if (n === null) problems[id] = CIFRA;
    else if (n <= 0) problems[id] = "Escribe el importe en positivo, mayor que cero.";
    if (pos.titular === "conyuge" && !spouseGiven) problems[`opt-pos-${pos.id}-titular`] = "Indica las cifras de tu cónyuge o pon esta inversión a tu nombre.";
    if (n === null || n <= 0) continue;
    const item: PosicionLatente = { importe: n, tipo: pos.tipo, titular: pos.titular };
    if (pos.descripcion.trim()) item.descripcion = pos.descripcion.trim();
    (pos.clase === "perdida" ? perdidas : ganancias).push(item);
  }
  // Every constraint, null / 0 / [] when not stated: the screen, not the case's stored preferences, decides the run.
  const restricciones: Restricciones = {
    aportacion_maxima_pensiones: pensionMax ?? null,
    aportacion_maxima_pensiones_conyuge: (spouseGiven ? pensionMaxConyuge : undefined) ?? null,
    acepta_iliquidez: v.iliquidez ? v.iliquidez === "si" : null,
    donativo_previsto: previsto ?? 0,
    presupuesto_donativos: presupuesto ?? null,
    donativo_recurrente_importe_anterior: recurrente ?? null,
    perdidas_latentes: perdidas,
    ganancias_latentes: ganancias,
  };
  if (fechaEdit !== null && !/^\d{4}-\d{2}-\d{2}$/.test(fechaEdit)) problems["opt-fecha"] = "Elige una fecha.";

  const body: OptimizeRequest = { datos, restricciones };
  if (ctx.caseId) body.case_id = ctx.caseId;
  else if (ctx.taxYear !== null) body.ejercicio = ctx.taxYear;
  // Married: the spouse's figures on screen, or explicitly none (the case's stored ones are not used).
  if (v.married) body.conyuge = spouseGiven ? conyuge : null;
  if (fechaEdit !== null && fechaEdit !== ctx.today) body.fecha = fechaEdit;
  // What the plan depends on: the case's facts and the figures typed over them, the spouse, the
  // constraints and the date. Saving what is on screen to the case leaves it unchanged (the spouse's
  // figures and the constraints count through the request, which carries them in full).
  const perfil: Record<string, unknown> = {};
  for (const fact of ctx.facts) if (!PLAN_INPUT_FACTS.has(fact.name)) perfil[fact.name] = fact.value;
  Object.assign(perfil, datos);
  const key = JSON.stringify({
    perfil: Object.fromEntries(Object.entries(perfil).sort(([a], [b]) => a.localeCompare(b))),
    conyuge: body.conyuge === undefined ? "sin cónyuge" : body.conyuge,
    restricciones,
    fecha: body.fecha ?? null,
    ejercicio: body.ejercicio ?? null,
    caseId: ctx.caseId,
  });
  return { problems, datos, spouseGiven, restricciones, body, key };
}
