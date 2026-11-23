// What «Guardar en mi expediente» writes from «Tus datos»: one fact per changed figure, plus the
// spouse's figures and the constraints as one fact each, written whole. Pure.

import { DATOS_CONYUGE, PREFERENCIAS_OPTIMIZACION, type StoredPosition, type StoredPreferences } from "../expediente";
import { parseAmount, stated } from "../formulario";
import { type BaseExpediente, LIMIT_NAMES, type Position, SPOUSE, rowsKey } from "./campos";
import type { FormularioOptimizador, Peticion, Vista } from "./peticion";

export type ValorHecho = string | number | boolean | null;

export interface Guardado {
  /** [fact, value | null to delete], in the order they are written. */
  pendientes: Array<[string, ValorHecho]>;
  /** How many changes that is, as the user counts them (each changed box is one). */
  cambios: number;
}

export function cambiosPendientes(form: FormularioOptimizador, base: BaseExpediente, v: Vista, p: Peticion): Guardado {
  const { baseline, storedSpouse, storedPrefs, spouseBaseline, limitsBaseline } = base;
  const pendientes: Array<[string, ValorHecho]> = [];
  for (const [name, value] of Object.entries(p.datos)) {
    if (name === "tributacion") continue;
    pendientes.push([name, name in baseline && value === 0 && !(form.edits[name] ?? "").trim() ? null : (value as string | number | boolean)]);
  }
  let cambios = pendientes.length;

  // The spouse's figures: one fact, written whole (figures this screen does not show are kept), only
  // when none of its boxes has a problem.
  const spouseOk = v.married && !SPOUSE.some((def) => p.problems[`opt-conyuge-${def.name}`]);
  const spouseChanges = spouseOk ? SPOUSE.filter((def) => stated(def.kind, v.spouseValue(def.name)) !== stated(def.kind, spouseBaseline[def.name])).length : 0;
  if (spouseChanges > 0) {
    const next: Record<string, unknown> = { ...(storedSpouse ?? {}) };
    for (const def of SPOUSE) {
      const now = stated(def.kind, v.spouseValue(def.name));
      if (!now) delete next[def.name];
      else next[def.name] = def.kind === "flag" ? true : Number(now);
    }
    // No box of the section left: the spouse's figures are removed (hidden ones alone would describe nobody).
    pendientes.push([DATOS_CONYUGE, SPOUSE.some((def) => def.name in next) ? JSON.stringify(next) : null]);
    cambios += spouseChanges;
  }

  // The constraints: one fact, written whole, only when none of them has a problem. What is hidden
  // (the spouse's limit and filing plan of a user who is not married) keeps its stored value.
  const prefProblem = Object.keys(p.problems).some((id) => LIMIT_NAMES.some((name) => id === `opt-${name}`) || id.startsWith("opt-pos-"));
  let prefChanges = 0;
  if (!prefProblem) {
    for (const name of LIMIT_NAMES) {
      if (name === "aportacion_maxima_pensiones_conyuge" && !v.married) continue;
      const zeroIsNone = name === "donativo_previsto";
      const now = stated("money", v.limitValue(name));
      const before = stated("money", limitsBaseline[name]);
      if ((zeroIsNone && now === "0" ? "" : now) !== (zeroIsNone && before === "0" ? "" : before)) prefChanges += 1;
    }
    if (v.iliquidez !== base.iliquidezBaseline) prefChanges += 1;
    if (rowsKey(v.positions) !== rowsKey(base.positionsBaseline)) prefChanges += 1;
    if (v.married && v.tributacionActual !== base.tributacionBaseline) prefChanges += 1;
  }
  if (prefChanges > 0) {
    const next: StoredPreferences = {};
    for (const name of LIMIT_NAMES) {
      const raw = name === "aportacion_maxima_pensiones_conyuge" && !v.married ? limitsBaseline[name] : v.limitValue(name);
      const n = parseAmount(raw);
      if (n !== null && !(name === "donativo_previsto" && n === 0)) next[name] = n;
    }
    if (v.iliquidez) next.acepta_iliquidez = v.iliquidez === "si";
    const row = (pos: Position): StoredPosition => {
      const item: StoredPosition = { id: pos.id, importe: parseAmount(pos.importe) ?? 0, tipo: pos.tipo, titular: pos.titular };
      if (pos.descripcion.trim()) item.descripcion = pos.descripcion.trim();
      return item;
    };
    const perdidas = v.positions.filter((pos) => pos.clase === "perdida").map(row);
    const ganancias = v.positions.filter((pos) => pos.clase === "ganancia").map(row);
    if (perdidas.length) next.perdidas_latentes = perdidas;
    if (ganancias.length) next.ganancias_latentes = ganancias;
    // The filing plan only once the user chose it (the screen's «individual» is a default, not a statement).
    const tributacion = v.married ? (form.tributacionEdit ?? storedPrefs?.tributacion_prevista) : storedPrefs?.tributacion_prevista;
    if (tributacion) next.tributacion_prevista = tributacion;
    pendientes.push([PREFERENCIAS_OPTIMIZACION, Object.keys(next).length ? JSON.stringify(next) : null]);
    cambios += prefChanges;
  }
  return { pendientes, cambios };
}

/** The form once `fact` is saved: what it held is now the case's, so its boxes go back to the case. */
export function trasGuardar(form: FormularioOptimizador, fact: string): FormularioOptimizador {
  const edits = { ...form.edits };
  delete edits[fact];
  const next: FormularioOptimizador = { ...form, edits };
  if (fact === "comunidad_autonoma") next.comunidadEdit = null;
  if (fact === "estado_civil") next.estadoEdit = null;
  if (fact === DATOS_CONYUGE) next.spouseEdits = {};
  if (fact === PREFERENCIAS_OPTIMIZACION) {
    next.limitEdits = {};
    next.iliquidezEdit = null;
    next.positionsEdit = null;
    next.tributacionEdit = null;
  }
  return next;
}
