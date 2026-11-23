// PestanasNavegacion: GitHub's UnderlineNav (the tabs under the header), with Primer's styles and tokens.
// Primer's own UnderlineNav writes its overflow button in English ("More"); this one says «Más» and keeps
// the same behaviour: the tabs that do not fit go into a menu, and the current one never hides in it.
//
// Every tab is a real link (`href`, so it opens in a new tab with Ctrl/⌘-click) whose plain click runs
// `alElegir` instead. `actual` marks the page on screen (aria-current="page").
//
// Accessible names are the visible text (WCAG 2.5.3): a counter adds its wording after the label for screen
// readers only («Avisos, 3 sin leer»). In «Más» the tabs are grouped by `grupo`, in the order of `grupos`.
//
//   <PestanasNavegacion aria-label="Secciones" pestanas={[{ id: "conversacion", etiqueta: "Conversación", href: "/", icono: CommentDiscussionIcon, actual: true, alElegir }]} />

import type { Icon } from "@primer/octicons-react";
import { ActionList, ActionMenu, CounterLabel, VisuallyHidden } from "@primer/react";
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
  /** What the counter means, read after the label («3 sin leer»). Without it, just the number. */
  contadorEtiqueta?: string;
  /** Section of the «Más» menu (its heading). */
  grupo?: string;
  /** Always in the bar, never in «Más» (like the current tab). */
  destacada?: boolean;
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
      {p.contador !== undefined && p.contador > 0 && <Contador p={p} />}
    </>
  );
}

function Contador({ p }: { p: Pestana }) {
  return (
    <span className={estilos.contador}>
      <span aria-hidden>
        <CounterLabel>{p.contador}</CounterLabel>
      </span>
      <VisuallyHidden>{`, ${p.contadorEtiqueta ?? p.contador}`}</VisuallyHidden>
    </span>
  );
}

/** The tabs of «Más» by section, in the order of `grupos` (those without a section, last). */
function porGrupo(resto: Pestana[], grupos: string[]): { nombre: string | null; pestanas: Pestana[] }[] {
  const salida: { nombre: string | null; pestanas: Pestana[] }[] = [];
  for (const nombre of [...grupos, null]) {
    const pestanas = resto.filter((p) => (nombre === null ? !p.grupo || !grupos.includes(p.grupo) : p.grupo === nombre));
    if (pestanas.length > 0) salida.push({ nombre, pestanas });
  }
  return salida;
}

export function PestanasNavegacion({
  pestanas,
  grupos = [],
  "aria-label": etiqueta,
}: {
  pestanas: Pestana[];
  /** The sections of «Más», in order. */
  grupos?: string[];
  "aria-label": string;
}) {
  const envoltura = useRef<HTMLElement | null>(null);
  const medidas = useRef<HTMLUListElement | null>(null);
  // How many of the other tabs fit after the fixed ones (the current one and the highlighted ones).
  const [caben, setCaben] = useState(pestanas.length);
  const clave = pestanas.map((p) => `${p.id}:${p.etiqueta}:${p.contador ?? ""}:${p.actual || p.destacada ? 1 : 0}`).join("|");
  const fija = (p: Pestana, i: number) => i === 0 || p.actual || p.destacada === true;

  useLayoutEffect(() => {
    const nav = envoltura.current;
    const lista = medidas.current;
    if (!nav || !lista) return;
    // Read from `clave` (it carries which tabs are fixed), so the effect depends on nothing else.
    const fijas = clave.split("|").map((c, i) => i === 0 || c.endsWith(":1"));
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
      // The fixed tabs and «Más» first; then the others, in order, while they fit.
      let usado = ANCHO_MAS + anchos.reduce((s, a, i) => (fijas[i] ? s + a + hueco : s), 0);
      let n = 0;
      for (const [i, a] of anchos.entries()) {
        if (fijas[i]) continue;
        if (usado + a + hueco > disponible) break;
        usado += a + hueco;
        n += 1;
      }
      setCaben(n);
    };
    calcular();
    const observador = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(calcular);
    observador?.observe(nav);
    return () => observador?.disconnect();
  }, [clave]);

  // The first tab, the current one and the highlighted ones never go into the menu; the rest, in order, as
  // many as fit.
  const visibles: Pestana[] = [];
  const resto: Pestana[] = [];
  let otras = 0;
  pestanas.forEach((p, i) => {
    if (fija(p, i)) visibles.push(p);
    else if (otras++ < caben) visibles.push(p);
    else resto.push(p);
  });

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
                  {porGrupo(resto, grupos).map((g, i) => {
                    const items = g.pestanas.map((p) => {
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
                              <Contador p={p} />
                            </ActionList.TrailingVisual>
                          )}
                        </ActionList.LinkItem>
                      );
                    });
                    if (g.nombre === null) return <ActionList.Group key="otros">{items}</ActionList.Group>;
                    return (
                      <ActionList.Group key={g.nombre}>
                        {i > 0 && <ActionList.Divider />}
                        <ActionList.GroupHeading>{g.nombre}</ActionList.GroupHeading>
                        {items}
                      </ActionList.Group>
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
