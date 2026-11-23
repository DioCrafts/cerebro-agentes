// PestanasNavegacion: GitHub's UnderlineNav (the tabs under the header), with Primer's styles and tokens.
// Primer's own UnderlineNav writes its overflow button in English ("More"); this one says «Más» and keeps
// the same behaviour: the tabs that do not fit go into a menu, and the current one never hides in it.
//
// Every tab is a real link (`href`, so it opens in a new tab with Ctrl/⌘-click) whose plain click runs
// `alElegir` instead. `actual` marks the page on screen (aria-current="page").
//
//   <PestanasNavegacion aria-label="Secciones" pestanas={[{ id: "conversacion", etiqueta: "Conversación", href: "/", icono: CommentDiscussionIcon, actual: true, alElegir }]} />

import type { Icon } from "@primer/octicons-react";
import { ActionList, ActionMenu, CounterLabel } from "@primer/react";
import type { MouseEvent } from "react";
import { useLayoutEffect, useRef, useState } from "react";

import estilos from "./PestanasNavegacion.module.css";

export interface Pestana {
  id: string;
  etiqueta: string;
  href: string;
  icono: Icon;
  /** Tooltip (a longer description). */
  titulo?: string;
  contador?: number;
  actual: boolean;
  alElegir: () => void;
}

/** Width kept for the «Más» button when some tabs overflow. */
const ANCHO_MAS = 96;

function clicNormal(e: MouseEvent): boolean {
  return e.button === 0 && !e.metaKey && !e.ctrlKey && !e.shiftKey && !e.altKey;
}

function Contenido({ p }: { p: Pestana }) {
  const Icono = p.icono;
  return (
    <>
      <span className={estilos.icono}>
        <Icono size={16} />
      </span>
      <span className={estilos.texto} data-content={p.etiqueta}>
        {p.etiqueta}
      </span>
      {p.contador !== undefined && p.contador > 0 && (
        <span className={estilos.contador}>
          <CounterLabel>{p.contador}</CounterLabel>
        </span>
      )}
    </>
  );
}

export function PestanasNavegacion({ pestanas, "aria-label": etiqueta }: { pestanas: Pestana[]; "aria-label": string }) {
  const envoltura = useRef<HTMLElement | null>(null);
  const medidas = useRef<HTMLUListElement | null>(null);
  const [caben, setCaben] = useState(pestanas.length);
  const clave = pestanas.map((p) => `${p.id}:${p.etiqueta}:${p.contador ?? ""}`).join("|");

  useLayoutEffect(() => {
    const nav = envoltura.current;
    const lista = medidas.current;
    if (!nav || !lista) return;
    const calcular = () => {
      const estilo = getComputedStyle(nav);
      const disponible = nav.clientWidth - parseFloat(estilo.paddingLeft) - parseFloat(estilo.paddingRight);
      const hueco = parseFloat(getComputedStyle(lista).columnGap) || 0;
      const anchos = Array.from(lista.children, (li) => (li as HTMLElement).offsetWidth);
      const total = anchos.reduce((s, a) => s + a + hueco, 0) - hueco;
      if (total <= disponible || anchos.every((a) => a === 0)) {
        setCaben(anchos.length);
        return;
      }
      let usado = ANCHO_MAS;
      let n = 0;
      for (const a of anchos) {
        if (usado + a + hueco > disponible) break;
        usado += a + hueco;
        n += 1;
      }
      setCaben(Math.max(n, 1));
    };
    calcular();
    const observador = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(calcular);
    observador?.observe(nav);
    return () => observador?.disconnect();
  }, [clave]);

  // The current tab is never hidden in the menu: it takes the last visible place.
  let visibles = pestanas.slice(0, caben);
  let resto = pestanas.slice(caben);
  const actualEnMenu = resto.find((p) => p.actual);
  if (actualEnMenu && visibles.length > 0) {
    const desplazada = visibles[visibles.length - 1];
    visibles = [...visibles.slice(0, -1), actualEnMenu];
    resto = [...(desplazada ? [desplazada] : []), ...resto.filter((p) => p !== actualEnMenu)];
  }

  const elegir = (p: Pestana) => (e: MouseEvent) => {
    if (!clicNormal(e)) return;
    e.preventDefault();
    p.alElegir();
  };

  return (
    <nav ref={envoltura} aria-label={etiqueta} className={estilos.envoltura}>
      <ul className={estilos.lista}>
        {visibles.map((p) => (
          <li key={p.id}>
            <a href={p.href} className={estilos.item} aria-current={p.actual ? "page" : undefined} title={p.titulo} data-panel={p.id} onClick={elegir(p)}>
              <Contenido p={p} />
            </a>
          </li>
        ))}
        {resto.length > 0 && (
          <li className={estilos.mas}>
            <span className={estilos.divisor} aria-hidden />
            <ActionMenu>
              <ActionMenu.Button variant="invisible">Más</ActionMenu.Button>
              <ActionMenu.Overlay width="medium">
                <ActionList>
                  {resto.map((p) => {
                    const Icono = p.icono;
                    return (
                      <ActionList.LinkItem key={p.id} href={p.href} onClick={elegir(p)} data-panel={p.id}>
                        <ActionList.LeadingVisual>
                          <Icono />
                        </ActionList.LeadingVisual>
                        {p.etiqueta}
                        {p.titulo && p.titulo !== p.etiqueta && <ActionList.Description variant="block">{p.titulo}</ActionList.Description>}
                        {p.contador !== undefined && p.contador > 0 && (
                          <ActionList.TrailingVisual>
                            <CounterLabel>{p.contador}</CounterLabel>
                          </ActionList.TrailingVisual>
                        )}
                      </ActionList.LinkItem>
                    );
                  })}
                </ActionList>
              </ActionMenu.Overlay>
            </ActionMenu>
          </li>
        )}
      </ul>
      {/* Every tab laid out once, invisible, to measure what fits. */}
      <ul ref={medidas} className={`${estilos.lista} ${estilos.medidas}`} aria-hidden>
        {pestanas.map((p) => (
          <li key={p.id}>
            <span className={estilos.item}>
              <Contenido p={p} />
            </span>
          </li>
        ))}
      </ul>
    </nav>
  );
}
