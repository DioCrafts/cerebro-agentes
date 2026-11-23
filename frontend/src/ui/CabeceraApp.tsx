// CabeceraApp: GitHub's global header (AppHeader). One row with the menu button, the mark, where you are
// (breadcrumbs), the search/command field and the actions; under it, the section tabs (UnderlineNav),
// passed as children. A "skip to content" link comes first for keyboard users.
//
//   <CabeceraApp
//     saltarA="contenido"
//     inicio={<IconButton icon={ThreeBarsIcon} aria-label="Abrir las consultas" variant="invisible" />}
//     marca={<Monogram size={32} />}
//     contexto={<Migas raiz="Cerebro" actual="Venta de vivienda" />}
//     busqueda={<BotonComandos onClick={…} />}
//     acciones={…}
//   >
//     <UnderlineNav aria-label="Secciones">…</UnderlineNav>
//   </CabeceraApp>

import { SearchIcon } from "@primer/octicons-react";
import { IconButton } from "@primer/react";
import type { Icon } from "@primer/octicons-react";
import type { ReactNode } from "react";

import estilos from "./CabeceraApp.module.css";

interface CabeceraAppProps {
  /** Id of the main content: the target of the skip link. */
  saltarA: string;
  inicio?: ReactNode;
  marca: ReactNode;
  contexto: ReactNode;
  busqueda?: ReactNode;
  acciones?: ReactNode;
  children?: ReactNode;
}

export function CabeceraApp({ saltarA, inicio, marca, contexto, busqueda, acciones, children }: CabeceraAppProps) {
  return (
    <header className={estilos.cabecera}>
      <a href={`#${saltarA}`} className={estilos.saltar}>
        Saltar al contenido
      </a>
      <div className={estilos.fila}>
        {inicio}
        {marca}
        <div className={estilos.contexto}>{contexto}</div>
        {busqueda}
        {acciones && <div className={estilos.acciones}>{acciones}</div>}
      </div>
      {children}
    </header>
  );
}

/** Where you are: "root / current", the current part in bold and truncated. The page's h1. */
export function Migas({ raiz, actual }: { raiz: ReactNode; actual?: ReactNode }) {
  return (
    <h1 className={estilos.migas}>
      <span className={estilos.migaRaiz}>{raiz}</span>
      {actual && (
        <>
          <span className={estilos.separador} aria-hidden>
            /
          </span>
          <span className={estilos.migaActual}>{actual}</span>
        </>
      )}
    </h1>
  );
}

/** The search / command field. A field-looking button on a computer, an icon button on a phone. */
export function BotonComandos({ onClick, texto = "Escribe / para buscar" }: { onClick: () => void; texto?: string }) {
  return (
    <>
      <button type="button" className={estilos.buscar} onClick={onClick} aria-keyshortcuts="/ Control+K Meta+K">
        <SearchIcon size={16} />
        <span className={estilos.buscarTexto}>{texto}</span>
        <kbd className={estilos.tecla} aria-hidden>
          /
        </kbd>
      </button>
      <span className={estilos.buscarIcono}>
        <IconButton icon={SearchIcon} aria-label={texto} variant="invisible" onClick={onClick} />
      </span>
    </>
  );
}

/** A thin vertical rule between groups of header actions. */
export function SeparadorCabecera() {
  return <span className={estilos.separadorVertical} aria-hidden />;
}

/** An icon button of the header with an optional dot (something new behind it). */
export function BotonCabecera({ icono, etiqueta, punto = false, onClick }: { icono: Icon; etiqueta: string; punto?: boolean; onClick: () => void }) {
  return (
    <span className={estilos.conPunto}>
      <IconButton icon={icono} aria-label={etiqueta} variant="invisible" onClick={onClick} />
      {punto && <span className={estilos.punto} aria-hidden />}
    </span>
  );
}
