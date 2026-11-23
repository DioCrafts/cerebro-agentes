// The state of the simulator: figures typed over the case, the detail of each child, and the last
// result of POST /simulate (debounced, last request wins). Pure parts live in lib/simulador.

import { useEffect, useMemo, useReducer, useRef } from "react";

import type { Fact, Simulation } from "../../api";
import { ApiError, simulate } from "../../api";
import { parseAmount } from "../../lib/formulario";
import { newId } from "../../lib/id";
import { OBLIGACION_SI_NO } from "../../lib/obligacion";
import { comunidadDeExpediente, formatMoney } from "../../lib/presentation";
import { FIELDS, FLAGS, type FieldDef } from "../../lib/simulador/campos";
import { type ApiChild, type ChildRow, childProblem, childToApi, derivedFromChildren, parseChildrenFact } from "../../lib/simulador/descendientes";

/** Figures to start from instead of the saved case (e.g. the optimiser's plan); read once, on mount. */
export interface SimulatorSeed {
  edits: Record<string, string>;
  tributacion?: string | null;
  comunidad?: string | null;
  /** Shown above the result while the seeded figures are on screen. */
  note?: string;
}

interface Estado {
  edits: Record<string, string>;
  tributacion: string;
  /** null = keep whatever the case says. */
  comunidadEdit: string | null;
  /** The detail of each child being edited; null = the one saved in the case. */
  childrenEdit: ChildRow[] | null;
  /** Breakdowns the user opened or closed by hand (otherwise open when they hold a figure). */
  openParts: Record<string, boolean>;
  simulation: Simulation | null;
  error: string | null;
  saving: boolean;
  saveError: string | null;
}

type Accion =
  | { type: "edit"; name: string; raw: string }
  | { type: "tributacion"; value: string }
  | { type: "comunidad"; value: string }
  | { type: "hijos"; rows: ChildRow[] }
  | { type: "desglose"; name: string; open: boolean }
  | { type: "resultado"; simulation: Simulation }
  | { type: "fallo"; error: string }
  | { type: "guardando" }
  | { type: "guardado" }
  | { type: "fallo-guardado"; error: string }
  | { type: "fin-guardado" }
  | { type: "deshacer" };

function reducer(state: Estado, action: Accion): Estado {
  switch (action.type) {
    case "edit":
      return { ...state, edits: { ...state.edits, [action.name]: action.raw } };
    case "tributacion":
      return { ...state, tributacion: action.value };
    case "comunidad":
      return { ...state, comunidadEdit: action.value };
    case "hijos":
      return { ...state, childrenEdit: action.rows };
    case "desglose":
      return { ...state, openParts: { ...state.openParts, [action.name]: action.open } };
    case "resultado":
      return { ...state, simulation: action.simulation, error: null };
    case "fallo":
      return { ...state, error: action.error };
    case "guardando":
      return { ...state, saving: true, saveError: null };
    case "guardado":
      return { ...state, edits: {}, comunidadEdit: null, childrenEdit: null };
    case "fallo-guardado":
      return { ...state, saveError: action.error };
    case "fin-guardado":
      return { ...state, saving: false };
    case "deshacer":
      return { ...state, edits: {}, tributacion: "individual", comunidadEdit: null, childrenEdit: null, saveError: null };
  }
}

export interface OpcionesSimulacion {
  open: boolean;
  caseId: string | null;
  facts: Fact[];
  taxPeriod: string | null;
  onSaveFact: (factName: string, value: number | string | boolean | null) => Promise<boolean>;
  seed: SimulatorSeed | null;
}

const NO_GUARDADO = "No se pudo guardar todo en tu expediente. Lo que no se guardó sigue en el simulador.";

export function useSimulacion({ open, caseId, facts, taxPeriod, onSaveFact, seed }: OpcionesSimulacion) {
  const [state, dispatch] = useReducer(reducer, seed, (s): Estado => ({
    edits: s?.edits ?? {},
    tributacion: s?.tributacion ?? "individual",
    comunidadEdit: s?.comunidad ?? null,
    childrenEdit: null,
    openParts: {},
    simulation: null,
    error: null,
    saving: false,
    saveError: null,
  }));
  const requestSeq = useRef(0);
  const { edits, tributacion, comunidadEdit, childrenEdit, simulation } = state;

  const baseline = useMemo(() => {
    const map: Record<string, string> = {};
    for (const fact of facts) {
      if (typeof fact.value === "number" || typeof fact.value === "boolean") map[fact.name] = String(fact.value);
    }
    return map;
  }, [facts]);
  const savedComunidad = useMemo(() => comunidadDeExpediente(facts.find((f) => f.name === "comunidad_autonoma")?.value), [facts]);
  const savedChildren = useMemo(() => parseChildrenFact(facts, newId), [facts]);
  const children = childrenEdit ?? savedChildren.rows;
  const year = simulation?.ejercicio ?? (taxPeriod && /^\d{4}/.test(taxPeriod) ? Number(taxPeriod.slice(0, 4)) : new Date().getFullYear());
  // Figures the detail of the children determines: shown read-only and never sent.
  const derived = useMemo(() => derivedFromChildren(children, year), [children, year]);
  const childProblems = useMemo(() => children.map((row) => childProblem(row, year)), [children, year]);

  const value = (name: string) => derived[name] ?? edits[name] ?? baseline[name] ?? "";
  // Totals smaller than their breakdown: the calculator would reject them, so they are not sent.
  const partNames = (field: FieldDef) => field.parts?.map((part) => part.name) ?? field.sumOf ?? [];
  const partsSum = (field: FieldDef) => partNames(field).reduce((sum, name) => sum + Math.max(0, parseAmount(value(name)) ?? 0), 0);
  const totalProblem = (field: FieldDef): string | null => {
    if (!field.parts && !field.sumOf) return null;
    const total = parseAmount(value(field.name)) ?? 0;
    const sum = partsSum(field);
    if (!(total > 0 && sum > total + 0.005)) return null;
    return field.sumOf
      ? `Los indicados en «Familia» suman ${sum}, más que este total (${total}). Sube el total o déjalo vacío.`
      : `El desglose suma ${formatMoney(sum)}, más que este total (${formatMoney(total)}). Sube el total o corrige el desglose.`;
  };
  const invalid = childProblems.some((problem) => problem !== null) || FIELDS.some((field) => totalProblem(field) !== null);

  const cambios = useMemo(() => {
    const out: Record<string, unknown> = {};
    for (const [name, raw] of Object.entries(edits)) {
      if (name in derived) continue;
      if (OBLIGACION_SI_NO.has(name)) {
        // «Sin indicar» keeps whatever the case says; «sí» / «no» are sent as booleans.
        if (raw !== "" && raw !== baseline[name]) out[name] = raw === "true";
        continue;
      }
      if (FLAGS.has(name)) {
        if (raw !== (baseline[name] ?? "false")) out[name] = raw === "true";
        continue;
      }
      const n = parseAmount(raw);
      if (n !== null && String(n) !== baseline[name]) out[name] = n;
    }
    if (tributacion !== "individual") out.tributacion = tributacion;
    if (comunidadEdit !== null && comunidadEdit !== savedComunidad) out.comunidad_autonoma = comunidadEdit === "Otra" ? "otra comunidad" : comunidadEdit || null;
    if (childrenEdit !== null || savedChildren.broken) {
      const detail = children.map(childToApi);
      // A saved detail that cannot be read is always replaced, even by an empty one.
      if (savedChildren.broken || JSON.stringify(detail) !== JSON.stringify(savedChildren.rows.map(childToApi))) out.descendientes = detail;
    }
    return out;
  }, [edits, tributacion, baseline, comunidadEdit, savedComunidad, derived, childrenEdit, children, savedChildren]);
  const cambiosKey = JSON.stringify(cambios);

  // Debounced, last-request-wins simulation. Nothing is sent while a figure is marked as
  // inconsistent: the last good result stays on screen.
  useEffect(() => {
    if (!open || invalid) return;
    const seq = ++requestSeq.current;
    const controller = new AbortController();
    const timer = setTimeout(() => {
      const requestYear = taxPeriod ? Number(taxPeriod.slice(0, 4)) : null;
      simulate({
        caseId,
        ejercicio: caseId ? null : requestYear,
        escenarios: [
          { nombre: "Tu expediente", cambios: {} },
          { nombre: "Simulación", cambios: JSON.parse(cambiosKey) as Record<string, unknown> },
        ],
        leversForLastScenario: true,
        signal: controller.signal,
      }).then(
        (result) => {
          if (seq === requestSeq.current) dispatch({ type: "resultado", simulation: result });
        },
        (err: unknown) => {
          if (controller.signal.aborted || seq !== requestSeq.current) return;
          dispatch({ type: "fallo", error: err instanceof ApiError ? err.message : "No se pudo calcular la simulación." });
        },
      );
    }, 300);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [open, caseId, taxPeriod, cambiosKey, facts, invalid]);

  async function saveAll() {
    dispatch({ type: "guardando" });
    try {
      for (const [name, changedValue] of Object.entries(cambios)) {
        if (name === "descendientes") {
          const detail = changedValue as ApiChild[];
          // An empty detail removes the saved one; otherwise it is stored as JSON text.
          if (detail.length === 0 && !savedChildren.saved) continue;
          if (!(await onSaveFact(name, detail.length > 0 ? JSON.stringify(detail) : null))) {
            dispatch({ type: "fallo-guardado", error: NO_GUARDADO });
            return;
          }
          continue;
        }
        const saveable = typeof changedValue === "number" || typeof changedValue === "boolean" || (name === "comunidad_autonoma" && typeof changedValue === "string");
        if (!saveable) continue;
        if (!(await onSaveFact(name, changedValue))) {
          dispatch({ type: "fallo-guardado", error: NO_GUARDADO });
          return;
        }
      }
      dispatch({ type: "guardado" });
    } catch {
      dispatch({ type: "fallo-guardado", error: NO_GUARDADO });
    } finally {
      dispatch({ type: "fin-guardado" });
    }
  }

  return {
    state,
    baseline,
    savedChildren,
    children,
    childProblems,
    year,
    derived,
    value,
    partsSum,
    totalProblem,
    invalid,
    cambios,
    changed: Object.keys(cambios).length > 0,
    comunidad: comunidadEdit ?? savedComunidad,
    acciones: {
      saveAll,
      setEdit: (name: string, raw: string) => dispatch({ type: "edit", name, raw }),
      setTributacion: (value: string) => dispatch({ type: "tributacion", value }),
      setComunidad: (value: string) => dispatch({ type: "comunidad", value }),
      setChildren: (rows: ChildRow[]) => dispatch({ type: "hijos", rows }),
      setDesglose: (name: string, open: boolean) => dispatch({ type: "desglose", name, open }),
      deshacer: () => dispatch({ type: "deshacer" }),
    },
  };
}
