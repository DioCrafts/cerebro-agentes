// Dialogo: the shared modal for panels. A HojaLateral (role="dialog" on a <section>, named by its h2 title)
// that also tells what is inside it which heading level comes next, so a panel never jumps from the h2 of
// its frame to an h4. Panels write their section titles with <TituloSeccion> instead of a fixed <h3>.
//
//   <Dialogo open={open} onClose={onClose} titulo="Avisos">
//     <TituloSeccion>Pendientes</TituloSeccion>   → <h3>
//   </Dialogo>
//
// NivelTitulo sets the level for other frames (PaginaPanel's title is also an h2).

import type { HTMLAttributes, ReactNode } from "react";
import { createContext, createElement, useContext } from "react";

import type { HojaLateralProps } from "./HojaLateral";
import { HojaLateral } from "./HojaLateral";

type Nivel = 1 | 2 | 3 | 4 | 5 | 6;

const ContextoNivel = createContext<Nivel>(1);

/** The level of the heading that frames `children` (their section titles go one below). */
export function NivelTitulo({
  nivel,
  children,
}: {
  nivel: Nivel;
  children: ReactNode;
}) {
  return (
    <ContextoNivel.Provider value={nivel}>{children}</ContextoNivel.Provider>
  );
}

/** A section title one level below its frame (h3 inside a Dialogo or a page panel). */
export function TituloSeccion({
  children,
  ...resto
}: HTMLAttributes<HTMLHeadingElement>) {
  const nivel = Math.min(useContext(ContextoNivel) + 1, 6);
  return createElement(`h${nivel}`, resto, children);
}

export function Dialogo({ children, ...props }: HojaLateralProps) {
  return (
    <HojaLateral {...props}>
      <NivelTitulo nivel={2}>{children}</NivelTitulo>
    </HojaLateral>
  );
}
