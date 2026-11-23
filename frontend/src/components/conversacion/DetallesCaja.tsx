// A Box that folds: GitHub's `<details class="Box">` (the collapsed «Files changed» or a long check run). The
// summary is the Box header; what it hides goes below it, in rows (`divide-y`) or a padded body.
//
// Candidate for src/ui, like EtiquetaEstado.

import { clsx } from "clsx";
import type { ReactNode } from "react";

import { CounterLabel, Details } from "../../ui";
import { ChevronRightIcon } from "../../ui/iconos";

interface DetallesCajaProps {
  /** The summary's text: the accessible name of the disclosure. */
  resumen: ReactNode;
  /** A count after the summary (CounterLabel). */
  contador?: number;
  /** An Octicon after the chevron. */
  icono?: ReactNode;
  /** Something at the right of the header (a Label). */
  extra?: ReactNode;
  /** Open on first render (problems the reader must see). */
  abierto?: boolean;
  className?: string;
  children: ReactNode;
}

export function DetallesCaja({ resumen, contador, icono, extra, abierto, className, children }: DetallesCajaProps) {
  return (
    <Details open={abierto || undefined} className={clsx("group rounded-medium border border-border-default bg-canvas-default", className)}>
      <Details.Summary className="flex cursor-pointer items-center gap-2 rounded-medium px-3 py-2 font-semibold text-fg-default hover:bg-canvas-muted group-open:rounded-b-none group-open:border-b group-open:border-border-default group-open:bg-canvas-muted">
        <ChevronRightIcon className="shrink-0 text-fg-muted transition-transform group-open:rotate-90" />
        {icono}
        <span className="min-w-0 flex-1">{resumen}</span>
        {extra}
        {contador !== undefined && <CounterLabel>{contador}</CounterLabel>}
      </Details.Summary>
      {children}
    </Details>
  );
}
