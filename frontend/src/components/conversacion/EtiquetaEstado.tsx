// GitHub's StateLabel (the filled pill of «Open», «Closed», «Merged») for the app's states: a claim's verdict, a
// council session, a phase. Primer's StateLabel paints only its issue/PR statuses, and every status with an icon
// gives that icon an English accessible name («Issue», «Pull request», «Alert»). So this wraps the two statuses
// without an icon (`open`, `closed`), recolours them with the other emphasis roles and takes its own Octicon.
//
// Candidate for src/ui (it belongs to no panel); kept here while the panel groups are migrated in parallel.

import { clsx } from "clsx";
import type { ReactNode } from "react";

import { StateLabel } from "../../ui";
import estilos from "./EtiquetaEstado.module.css";

export type TonoEstado = "exito" | "atencion" | "peligro" | "hecho" | "acento" | "neutro";

interface EtiquetaEstadoProps {
  tono: TonoEstado;
  /** An Octicon (aria-hidden) or a small Spinner before the text. */
  visual?: ReactNode;
  size?: "small" | "medium";
  className?: string;
  children: ReactNode;
}

export function EtiquetaEstado({ tono, visual, size = "small", className, children }: EtiquetaEstadoProps) {
  return (
    <StateLabel
      status={tono === "hecho" ? "closed" : "open"}
      size={size}
      className={clsx(tono !== "exito" && tono !== "hecho" && estilos[tono], className)}
    >
      {visual && (
        <span className={estilos.visual} aria-hidden>
          {visual}
        </span>
      )}
      {children}
    </StateLabel>
  );
}
