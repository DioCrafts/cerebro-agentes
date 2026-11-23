// The state of «Optimizar mis impuestos» in one reducer: what is typed over the case, the tab, the
// plan last computed and the save. The request and the save themselves are pure (lib/optimizador).

import { type FormEvent, type RefObject, useEffect, useMemo, useReducer, useRef, useState } from "react";
import { flushSync } from "react-dom";

import type { Fact } from "../../api";
import { ApiError } from "../../api";
import type { PlanOptimo } from "../../apiOptimizador";
import { optimizar } from "../../apiOptimizador";
import { newId } from "../../lib/id";
import {
  type BaseExpediente,
  EXTRA_NAMES,
  FIELD_BY_NAME,
  type LimitName,
  type Position,
  SPOUSE,
  baseDeExpediente,
  nuevaPosicion,
} from "../../lib/optimizador/campos";
import { type ValorHecho, cambiosPendientes, trasGuardar } from "../../lib/optimizador/guardado";
import { type FormularioOptimizador, FORMULARIO_VACIO, construirPeticion, resolver } from "../../lib/optimizador/peticion";
import { amountText, humanizeOptimizeError, parseAmount, planEnSimulador, resultadoTexto, todayInSpain } from "../../lib/optimizador/textos";
import type { SimulatorSeed } from "../SimulatorPanel";

export type Tab = "datos" | "plan";

export interface Computed {
  plan: PlanOptimo;
  key: string;
  /** The figures the plan was computed from (to carry it over to the simulator). */
  datos: Record<string, unknown>;
  comunidad: string | null;
}

interface Estado {
  form: FormularioOptimizador;
  tab: Tab;
  cifrasOpen: boolean | null;
  sections: Record<string, boolean>;
  computed: Computed | null;
  loading: boolean;
  error: string | null;
  saving: boolean;
  saveError: string | null;
}

type Accion =
  | { type: "form"; update: (form: FormularioOptimizador) => FormularioOptimizador }
  | { type: "tab"; tab: Tab }
  | { type: "cifras"; open: boolean }
  | { type: "section"; name: string; open: boolean }
  | { type: "calculando" }
  | { type: "calculado"; computed: Computed }
  | { type: "fallo"; error: string }
  | { type: "fin-calculo" }
  | { type: "guardando" }
  | { type: "guardado"; fact: string }
  | { type: "fallo-guardado"; error: string }
  | { type: "fin-guardado" };

const INICIAL: Estado = {
  form: FORMULARIO_VACIO,
  tab: "datos",
  cifrasOpen: null,
  sections: {},
  computed: null,
  loading: false,
  error: null,
  saving: false,
  saveError: null,
};

function reducer(state: Estado, action: Accion): Estado {
  switch (action.type) {
    case "form":
      return { ...state, form: action.update(state.form) };
    case "tab":
      return { ...state, tab: action.tab };
    case "cifras":
      return { ...state, cifrasOpen: action.open };
    case "section":
      return { ...state, sections: { ...state.sections, [action.name]: action.open } };
    case "calculando":
      return { ...state, loading: true, error: null };
    case "calculado":
      return { ...state, computed: action.computed, tab: "plan" };
    case "fallo":
      return { ...state, error: action.error };
    case "fin-calculo":
      return { ...state, loading: false };
    case "guardando":
      return { ...state, saving: true, saveError: null };
    case "guardado":
      return { ...state, form: trasGuardar(state.form, action.fact) };
    case "fallo-guardado":
      return { ...state, saveError: action.error };
    case "fin-guardado":
      return { ...state, saving: false };
  }
}

export interface OpcionesOptimizador {
  caseId: string | null;
  facts: Fact[];
  taxPeriod: string | null;
  onSaveFact?: (factName: string, value: ValorHecho) => Promise<boolean>;
  onOpenSimulator: () => void;
  onUseInSimulator?: (seed: SimulatorSeed) => void;
  /** The scrolling area both tabs share, and the plan's headline (focused once a plan arrives). */
  scrollRef: RefObject<HTMLDivElement | null>;
  headlineRef: RefObject<HTMLElement | null>;
}

function enfocar(id: string | undefined) {
  if (!id) return;
  const el = document.getElementById(id);
  el?.scrollIntoView({ block: "center" });
  el?.focus();
}

export function useOptimizador({ caseId, facts, taxPeriod, onSaveFact, onOpenSimulator, onUseInSimulator, scrollRef, headlineRef }: OpcionesOptimizador) {
  const [state, dispatch] = useReducer(reducer, INICIAL);
  const [today] = useState(todayInSpain);
  const abortRef = useRef<AbortController | null>(null);

  // A request still running when the panel goes away is cancelled.
  useEffect(() => () => abortRef.current?.abort(), []);

  const base: BaseExpediente = useMemo(() => baseDeExpediente(facts), [facts]);
  const { form, computed } = state;
  const vista = resolver(form, base);
  const taxYear = taxPeriod && /^\d{4}/.test(taxPeriod) ? Number(taxPeriod.slice(0, 4)) : null;
  const peticion = construirPeticion(form, base, vista, { facts, caseId, taxYear, today });
  const guardado = cambiosPendientes(form, base, vista, peticion);
  const problemIds = Object.keys(peticion.problems);
  const stale = computed !== null && computed.key !== peticion.key;
  const year = taxYear ?? computed?.plan.ejercicio ?? Number(today.slice(0, 4));
  const conExpediente = Boolean(caseId);
  const caseHasIncome = ["rendimiento_trabajo_bruto", "rendimiento_actividad_neto"].some((name) => name in base.baseline);

  const setForm = (update: (f: FormularioOptimizador) => FormularioOptimizador) => dispatch({ type: "form", update });
  /** Edit the rows (the first edit starts from the stored ones). */
  const setPositions = (update: (prev: Position[]) => Position[]) =>
    setForm((f) => ({ ...f, positionsEdit: update(f.positionsEdit ?? base.positionsBaseline) }));

  async function calcular(event?: FormEvent) {
    event?.preventDefault();
    if (problemIds.length > 0) {
      flushSync(() => {
        dispatch({ type: "tab", tab: "datos" });
        if (problemIds.some((id) => FIELD_BY_NAME.has(id.slice(4)))) dispatch({ type: "cifras", open: true });
      });
      enfocar(problemIds[0]);
      return;
    }
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    const snapshot = { key: peticion.key, datos: { ...peticion.datos }, comunidad: form.comunidadEdit };
    dispatch({ type: "calculando" });
    try {
      const plan = await optimizar(peticion.body, controller.signal);
      flushSync(() => dispatch({ type: "calculado", computed: { plan, ...snapshot } }));
      headlineRef.current?.focus();
      headlineRef.current?.scrollIntoView({ block: "start" });
    } catch (err) {
      if (controller.signal.aborted) return;
      dispatch({
        type: "fallo",
        error: err instanceof ApiError ? humanizeOptimizeError(err.message, err.status) : "No se pudo calcular el plan: comprueba tu conexión e inténtalo de nuevo.",
      });
    } finally {
      if (abortRef.current === controller) {
        abortRef.current = null;
        dispatch({ type: "fin-calculo" });
      }
    }
  }

  /** Take the user to the place where a missing figure is typed (or add it inline). */
  function completar(name: string) {
    let target: string | null = null;
    if (name === "conyuge") target = `opt-conyuge-${SPOUSE[0]?.name ?? ""}`;
    else if (name === "estado_civil" || name === "comunidad_autonoma") target = `opt-${name}`;
    else if (name === "perdidas_latentes") {
      const existing = vista.positions.find((p) => p.clase === "perdida");
      const row = existing ?? nuevaPosicion("perdida", newId());
      target = `opt-pos-${row.id}-importe`;
      if (!existing) flushSync(() => setPositions((prev) => [...prev, row]));
    } else if (FIELD_BY_NAME.has(name)) target = `opt-${name}`;
    if (target === null) {
      onOpenSimulator();
      return;
    }
    flushSync(() => {
      dispatch({ type: "tab", tab: "datos" });
      if (name === "conyuge" && !vista.married) setForm((f) => ({ ...f, estadoEdit: "casado" }));
      if (FIELD_BY_NAME.has(name) || name === "estado_civil" || name === "comunidad_autonoma") dispatch({ type: "cifras", open: true });
      if (EXTRA_NAMES.has(name)) setForm((f) => (f.extraFields.includes(name) ? f : { ...f, extraFields: [...f.extraFields, name] }));
    });
    enfocar(target);
  }

  /** Switch tab and start it from the top (the two tabs share one scrolling area). */
  function showTab(next: Tab) {
    flushSync(() => dispatch({ type: "tab", tab: next }));
    if (scrollRef.current) scrollRef.current.scrollTop = 0;
  }

  function goToData() {
    showTab("datos");
    document.getElementById("opt-tab-datos")?.focus();
  }

  async function saveToCase() {
    if (!onSaveFact) return;
    dispatch({ type: "guardando" });
    try {
      for (const [name, value] of guardado.pendientes) {
        if (!(await onSaveFact(name, value))) {
          dispatch({ type: "fallo-guardado", error: "No se pudo guardar todo en tu expediente. Lo que no se guardó sigue en el formulario." });
          return;
        }
        dispatch({ type: "guardado", fact: name });
      }
    } catch {
      dispatch({ type: "fallo-guardado", error: "No se pudo guardar en tu expediente: comprueba tu conexión e inténtalo de nuevo." });
    } finally {
      dispatch({ type: "fin-guardado" });
    }
  }

  /** Back to what the case says, everywhere on «Tus datos» (the date of the plan is not in the case). */
  function undoChanges() {
    setForm((f) => ({ ...FORMULARIO_VACIO, extraFields: f.extraFields, fechaEdit: f.fechaEdit }));
  }

  function sendToSimulator() {
    if (!computed || !onUseInSimulator) return;
    const { plan } = computed;
    const figure = (name: string) => {
      const typed = computed.datos[name];
      if (typeof typed === "number") return typed;
      return parseAmount(base.baseline[name]) ?? 0;
    };
    const mapped = planEnSimulador(plan, figure);
    if (!mapped.ok) return;
    const seedEdits: Record<string, string> = {};
    for (const [name, v] of Object.entries(computed.datos)) {
      if (typeof v === "number") seedEdits[name] = amountText(v);
      else if (typeof v === "boolean") seedEdits[name] = String(v);
    }
    for (const [name, v] of Object.entries(mapped.cambios)) seedEdits[name] = amountText(v);
    onUseInSimulator({
      edits: seedEdits,
      tributacion: mapped.tributacion,
      comunidad: computed.comunidad,
      note: `Cifras del plan óptimo cargadas: con ellas el plan da ${resultadoTexto(plan.optimo.resultado_total)}. Si cambias algo aquí, el plan deja de ser el óptimo calculado.`,
    });
  }

  function addPosition(clase: Position["clase"]) {
    const row = nuevaPosicion(clase, newId());
    flushSync(() => setPositions((prev) => [...prev, row]));
    document.getElementById(`opt-pos-${row.id}-importe`)?.focus();
  }

  function addExtra(name: string) {
    flushSync(() => setForm((f) => ({ ...f, extraFields: [...f.extraFields, name] })));
    document.getElementById(`opt-${name}`)?.focus();
  }

  const section = (name: string, fallback = false) => state.sections[name] ?? fallback;

  return {
    state,
    base,
    vista,
    peticion,
    guardado,
    problemIds,
    stale,
    today,
    year,
    conExpediente,
    showCifras: state.cifrasOpen ?? !(conExpediente && caseHasIncome),
    acciones: {
      calcular,
      completar,
      showTab,
      goToData,
      saveToCase,
      undoChanges,
      sendToSimulator,
      addPosition,
      addExtra,
      setPositions,
      setPosition: (id: string, patch: Partial<Position>) => setPositions((prev) => prev.map((p) => (p.id === id ? { ...p, ...patch } : p))),
      setEdit: (name: string, raw: string) => setForm((f) => ({ ...f, edits: { ...f.edits, [name]: raw } })),
      setSpouseEdit: (name: string, raw: string) => setForm((f) => ({ ...f, spouseEdits: { ...f.spouseEdits, [name]: raw } })),
      setLimit: (name: LimitName, raw: string) => setForm((f) => ({ ...f, limitEdits: { ...f.limitEdits, [name]: raw } })),
      setForm,
      setCifrasOpen: (open: boolean) => dispatch({ type: "cifras", open }),
      section,
      toggleSection: (name: string, fallback = false) => dispatch({ type: "section", name, open: !section(name, fallback) }),
    },
  };
}

export type Optimizador = ReturnType<typeof useOptimizador>;
export type { ValorHecho };
