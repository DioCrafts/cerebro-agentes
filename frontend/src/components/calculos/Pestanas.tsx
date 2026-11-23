// In-page tabs of the calculators («1. Tus datos» / «2. Tu plan», «Mi actividad» / «130» / «303»): the ARIA
// tab pattern (arrow keys, Home and End) with GitHub's underline look. Each tab is `<idBase>-tab-<id>` and
// controls `<idBase>-tabpanel`, which the caller renders with `role="tabpanel"`.

import type { ReactNode } from "react";

import estilos from "./Pestanas.module.css";

export interface Pestana<T extends string> {
  id: T;
  etiqueta: ReactNode;
  /** After the label: a check, a «desactualizado» label… */
  extra?: ReactNode;
}

interface Props<T extends string> {
  /** Accessible name of the tab list. */
  etiqueta: string;
  idBase: string;
  pestanas: ReadonlyArray<Pestana<T>>;
  actual: T;
  onCambiar: (id: T) => void;
}

export function Pestanas<T extends string>({ etiqueta, idBase, pestanas, actual, onCambiar }: Props<T>) {
  function mover(desde: number, paso: number | "inicio" | "fin") {
    const n = pestanas.length;
    const destino = paso === "inicio" ? 0 : paso === "fin" ? n - 1 : (desde + paso + n) % n;
    const siguiente = pestanas[destino];
    if (!siguiente) return;
    onCambiar(siguiente.id);
    document.getElementById(`${idBase}-tab-${siguiente.id}`)?.focus();
  }
  return (
    <div role="tablist" aria-label={etiqueta} className={estilos.lista}>
      {pestanas.map((p, i) => (
        <button
          key={p.id}
          type="button"
          role="tab"
          id={`${idBase}-tab-${p.id}`}
          aria-selected={actual === p.id}
          aria-controls={`${idBase}-tabpanel`}
          tabIndex={actual === p.id ? 0 : -1}
          onClick={() => onCambiar(p.id)}
          onKeyDown={(e) => {
            const paso = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : e.key === "Home" ? "inicio" : e.key === "End" ? "fin" : null;
            if (paso === null) return;
            e.preventDefault();
            mover(i, paso);
          }}
          className={estilos.pestana}
        >
          {p.etiqueta}
          {p.extra}
        </button>
      ))}
    </div>
  );
}
