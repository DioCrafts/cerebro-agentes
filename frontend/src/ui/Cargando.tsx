// Loading states: skeletons with the shape of what is coming (Primer's SkeletonText / SkeletonBox), announced
// once to screen readers. Never a bare spinner for content; a Spinner only inside a button or next to a word.
//
//   {cargando ? <CargandoLista filas={3} etiqueta="Cargando avisos" /> : <Lista … />}

import { SkeletonBox, VisuallyHidden } from "@primer/react";
import { SkeletonText } from "@primer/react/experimental";

import { CajaFila, CajaLista, CajaRaiz } from "./Caja";

/** Rows of a list (a Caja with title-and-two-lines rows). */
export function CargandoLista({ filas = 3, etiqueta = "Cargando…" }: { filas?: number; etiqueta?: string }) {
  return (
    <div role="status">
      <VisuallyHidden>{etiqueta}</VisuallyHidden>
      <CajaRaiz aria-hidden>
        <CajaLista>
          {Array.from({ length: filas }, (_, i) => (
            <CajaFila key={i}>
              <SkeletonText size="titleSmall" maxWidth="40%" />
              <div className="mt-2">
                <SkeletonText lines={2} />
              </div>
            </CajaFila>
          ))}
        </CajaLista>
      </CajaRaiz>
    </div>
  );
}

/** A block of text (a paragraph or an answer on its way). */
export function CargandoTexto({ lineas = 3, etiqueta = "Cargando…" }: { lineas?: number; etiqueta?: string }) {
  return (
    <div role="status">
      <VisuallyHidden>{etiqueta}</VisuallyHidden>
      <div aria-hidden>
        <SkeletonText lines={lineas} />
      </div>
    </div>
  );
}

/** A box of a known height (a chart, a card). */
export function CargandoBloque({ alto = "8rem", etiqueta = "Cargando…" }: { alto?: string; etiqueta?: string }) {
  return (
    <div role="status">
      <VisuallyHidden>{etiqueta}</VisuallyHidden>
      <SkeletonBox height={alto} aria-hidden />
    </div>
  );
}
