// The turn still running, kept outside React state: the answer streams token by token, and only the
// component that draws it (Hilo's live bubble) subscribes. The rest of the shell — the panels, the
// sidebar, the composer — no longer re-renders on every token.

import { useSyncExternalStore } from "react";

import type { ProgressEvent } from "../api";

// A turn that is still running: the question plus the agent's live progress.
export interface PendingTurn {
  text: string;
  events: ProgressEvent[];
  startedAt: number;
  // The answer text streamed so far for the current model step (unverified).
  draft: string;
  // The household brain's events (plan, specialists, synthesis, figures gate), kept apart from the activity rows.
  cerebro?: ProgressEvent[];
}

export interface AlmacenTurno {
  get: () => PendingTurn | null;
  set: (next: PendingTurn | null | ((prev: PendingTurn | null) => PendingTurn | null)) => void;
  subscribe: (listener: () => void) => () => void;
}

export function crearAlmacenTurno(): AlmacenTurno {
  let actual: PendingTurn | null = null;
  const oyentes = new Set<() => void>();
  return {
    get: () => actual,
    set: (next) => {
      const valor = typeof next === "function" ? next(actual) : next;
      if (valor === actual) return;
      actual = valor;
      for (const oyente of oyentes) oyente();
    },
    subscribe: (oyente) => {
      oyentes.add(oyente);
      return () => oyentes.delete(oyente);
    },
  };
}

/** The running turn, re-rendering only the component that calls it. */
export function useTurnoEnCurso(almacen: AlmacenTurno): PendingTurn | null {
  return useSyncExternalStore(almacen.subscribe, almacen.get, almacen.get);
}
