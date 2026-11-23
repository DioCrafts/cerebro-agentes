// Caja (exported from src/ui as `Caja` with `Caja.Cabecera`, `Caja.Lista`…): GitHub's "Box" (the bordered container of lists, settings and cards): optional header, rows,
// body and footer. Primer React no longer ships it as a component, so it lives here on Primer tokens.
//
//   <Caja aria-label="Avisos">
//     <Caja.Cabecera titulo="Plazos" acciones={<Button size="small">Actualizar</Button>} />
//     <Caja.Lista>
//       <Caja.Fila>…</Caja.Fila>
//     </Caja.Lista>
//     <Caja.Pie>…</Caja.Pie>
//   </Caja>

import { clsx } from "clsx";
import type { HTMLAttributes, ReactNode } from "react";

import estilos from "./Caja.module.css";

type Variante = "normal" | "acento" | "peligro";

interface CajaProps extends HTMLAttributes<HTMLElement> {
  /** `section` with an accessible name when it groups content; `div` otherwise. */
  as?: "div" | "section" | "article" | "li";
  variante?: Variante;
  /** Rows with 8px vertical padding instead of 16px (dense lists). */
  condensada?: boolean;
}

export function CajaRaiz({ as: Elemento = "div", variante = "normal", condensada = false, className, ...resto }: CajaProps) {
  return (
    <Elemento
      className={clsx(estilos.caja, variante !== "normal" && estilos[variante], condensada && estilos.condensada, className)}
      {...resto}
    />
  );
}

interface CabeceraProps extends Omit<HTMLAttributes<HTMLDivElement>, "title"> {
  titulo?: ReactNode;
  /** Heading level of the title (h2 in a page, h3 inside a panel section). */
  nivel?: 2 | 3 | 4;
  acciones?: ReactNode;
}

export function CajaCabecera({ titulo, nivel = 3, acciones, className, children, ...resto }: CabeceraProps) {
  const Titulo = `h${nivel}` as const;
  return (
    <div className={clsx(estilos.cabecera, className)} {...resto}>
      {titulo !== undefined && <Titulo className={estilos.titulo}>{titulo}</Titulo>}
      {children}
      {acciones}
    </div>
  );
}

export function CajaCuerpo({ className, ...resto }: HTMLAttributes<HTMLDivElement>) {
  return <div className={clsx(estilos.cuerpo, className)} {...resto} />;
}

export function CajaLista({ className, ...resto }: HTMLAttributes<HTMLUListElement>) {
  return <ul className={clsx(estilos.lista, className)} {...resto} />;
}

export function CajaFila({ className, ...resto }: HTMLAttributes<HTMLLIElement>) {
  return <li className={clsx(estilos.fila, className)} {...resto} />;
}

export function CajaPie({ className, ...resto }: HTMLAttributes<HTMLDivElement>) {
  return <div className={clsx(estilos.pie, className)} {...resto} />;
}
