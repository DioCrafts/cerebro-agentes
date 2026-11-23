// The shell's navigation, built from the panel registry (src/paneles/registro.ts).
//
// The highlighted panels (avisos, módulos, expediente) are always in the top bar; on a wide screen the first
// tools follow them; everything else is in «Más», a menu that drops down on a computer and rises as a
// bottom sheet on a phone. Keyboard: the menu opens with Enter/Space/↓, moves with ↑/↓/Home/End, closes
// with Escape (focus goes back to «Más») and with Tab.

import clsx from "clsx";
import { Ellipsis } from "lucide-react";
import type { KeyboardEvent } from "react";
import { useEffect, useId, useRef, useState, useSyncExternalStore } from "react";

import type { DefinicionPanel, Insignias } from "../paneles/tipos";

// How many tools fit next to the highlighted panels (the sidebar takes 290 px from `lg` up): Tailwind
// `2xl` and `xl`. Below that, every tool is in «Más».
const MUY_ANCHO = "(min-width: 1536px)";
const ANCHO = "(min-width: 1280px)";

function useMedia(query: string): boolean {
  return useSyncExternalStore(
    (avisar) => {
      const mql = window.matchMedia?.(query);
      mql?.addEventListener("change", avisar);
      return () => mql?.removeEventListener("change", avisar);
    },
    () => window.matchMedia?.(query).matches ?? false,
    () => false,
  );
}

const BOTON =
  "relative flex items-center gap-2 rounded-lg border border-line bg-surface px-2 py-2 text-[13.5px] font-semibold text-ink-soft transition hover:border-primary hover:text-primary-dark focus-visible:outline-solid focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary sm:px-3";

function Insignia({ valor, primaria }: { valor: number; primaria: boolean }) {
  return primaria ? (
    <span className="rounded-pill bg-primary px-1.5 text-[11px] font-bold leading-[18px] text-white" aria-hidden>
      {valor}
    </span>
  ) : (
    <span
      className="absolute -right-1.5 -top-1.5 min-w-[18px] rounded-pill bg-risk-high-fg px-1 text-center text-[11px] font-bold leading-[18px] text-white"
      aria-hidden
    >
      {valor}
    </span>
  );
}

interface NavegacionProps {
  paneles: DefinicionPanel[];
  insignias: Insignias;
  abierto: string | null;
  onAbrir: (id: string) => void;
}

export function Navegacion({ paneles, insignias, abierto, onAbrir }: NavegacionProps) {
  const muyAncho = useMedia(MUY_ANCHO);
  const ancho = useMedia(ANCHO);
  const enLaBarra = muyAncho ? 4 : ancho ? 2 : 0;
  const destacados = paneles.filter((p) => p.destacado);
  const herramientas = paneles.filter((p) => !p.destacado);
  const enBarra = herramientas.slice(0, enLaBarra);
  const enMenu = herramientas.slice(enLaBarra);
  // Highlighted first except «Tu expediente», which closes the bar as before.
  const antes = destacados.filter((p) => p.id !== "expediente");
  const despues = destacados.filter((p) => p.id === "expediente");

  const boton = (p: DefinicionPanel) => {
    const insignia = p.insignia?.(insignias) ?? null;
    const Icono = p.icono;
    const esExpediente = p.id === "expediente";
    const soloIcono = p.id === "avisos";
    return (
      <button
        key={p.id}
        type="button"
        onClick={() => onAbrir(p.id)}
        aria-label={insignia && !esExpediente ? insignia.etiqueta : p.titulo}
        aria-haspopup="dialog"
        aria-expanded={abierto === p.id}
        title={p.titulo}
        data-panel={p.id}
        className={clsx(BOTON, soloIcono && "px-2!")}
      >
        <Icono size={16} aria-hidden />
        {!soloIcono && <span className={clsx(esExpediente ? "hidden sm:inline" : "hidden md:inline")}>{p.etiqueta}</span>}
        {insignia && (
          <>
            <Insignia valor={insignia.valor} primaria={esExpediente} />
            {esExpediente && <span className="sr-only">{insignia.etiqueta}</span>}
          </>
        )}
      </button>
    );
  };

  return (
    <nav aria-label="Herramientas" className="flex items-center gap-1.5 sm:gap-2">
      {antes.map(boton)}
      {enBarra.map(boton)}
      {enMenu.length > 0 && <MenuMas paneles={enMenu} insignias={insignias} onAbrir={onAbrir} />}
      {despues.map(boton)}
    </nav>
  );
}

function MenuMas({ paneles, insignias, onAbrir }: { paneles: DefinicionPanel[]; insignias: Insignias; onAbrir: (id: string) => void }) {
  const [abierto, setAbierto] = useState(false);
  const botonRef = useRef<HTMLButtonElement | null>(null);
  const menuRef = useRef<HTMLDivElement | null>(null);
  const id = useId();
  const pendientes = paneles.reduce((n, p) => n + (p.insignia?.(insignias)?.valor ?? 0), 0);

  function elementos(): HTMLElement[] {
    return Array.from(menuRef.current?.querySelectorAll<HTMLElement>('[role="menuitem"]') ?? []);
  }

  function cerrar(devolverFoco: boolean) {
    setAbierto(false);
    if (devolverFoco) botonRef.current?.focus();
  }

  useEffect(() => {
    if (!abierto) return;
    elementos()[0]?.focus();
    const fuera = (e: PointerEvent) => {
      const objetivo = e.target as Node;
      if (!menuRef.current?.contains(objetivo) && !botonRef.current?.contains(objetivo)) setAbierto(false);
    };
    document.addEventListener("pointerdown", fuera);
    return () => document.removeEventListener("pointerdown", fuera);
  }, [abierto]);

  function teclado(e: KeyboardEvent<HTMLDivElement>) {
    const items = elementos();
    const actual = items.indexOf(document.activeElement as HTMLElement);
    const mover = (i: number) => items[(i + items.length) % items.length]?.focus();
    if (e.key === "ArrowDown") mover(actual + 1);
    else if (e.key === "ArrowUp") mover(actual - 1);
    else if (e.key === "Home") mover(0);
    else if (e.key === "End") mover(items.length - 1);
    else if (e.key === "Escape") {
      e.stopPropagation();
      cerrar(true);
    } else if (e.key === "Tab") {
      cerrar(false);
      return;
    } else return;
    e.preventDefault();
  }

  return (
    <div className="relative">
      <button
        ref={botonRef}
        type="button"
        aria-haspopup="menu"
        aria-expanded={abierto}
        aria-controls={abierto ? id : undefined}
        aria-label={pendientes > 0 ? `Más herramientas (${pendientes} pendientes)` : "Más herramientas"}
        title="Más herramientas"
        onClick={() => setAbierto((v) => !v)}
        onKeyDown={(e) => {
          if (e.key === "ArrowDown" && !abierto) {
            e.preventDefault();
            setAbierto(true);
          }
        }}
        className={BOTON}
      >
        <Ellipsis size={16} aria-hidden />
        <span className="hidden md:inline">Más</span>
        {pendientes > 0 && <Insignia valor={pendientes} primaria={false} />}
      </button>
      {abierto && (
        <>
          {/* Phone: a bottom sheet over a dimmed page. */}
          <div className="fixed inset-0 z-40 bg-ink/40 sm:hidden" aria-hidden onClick={() => cerrar(true)} />
          <div
            ref={menuRef}
            id={id}
            role="menu"
            tabIndex={-1}
            aria-label="Más herramientas"
            onKeyDown={teclado}
            className={clsx(
              "z-50 border-line bg-paper shadow-pop",
              // Phone: anchored to the bottom of the screen, above the home indicator.
              "fixed inset-x-0 bottom-0 max-h-[75dvh] overflow-y-auto rounded-t-2xl border-t px-2 pb-[max(env(safe-area-inset-bottom),12px)] pt-2",
              // Tablet and up: a dropdown under the button.
              "sm:absolute sm:inset-x-auto sm:bottom-auto sm:right-0 sm:top-full sm:mt-2 sm:w-72 sm:rounded-xl sm:border sm:p-1.5",
            )}
          >
            <div className="mx-auto mb-2 h-1 w-10 rounded-full bg-line sm:hidden" aria-hidden />
            {paneles.map((p) => {
              const Icono = p.icono;
              const insignia = p.insignia?.(insignias) ?? null;
              return (
                <button
                  key={p.id}
                  type="button"
                  role="menuitem"
                  tabIndex={-1}
                  data-panel={p.id}
                  onClick={() => {
                    // Focus goes back to «Más» first, so closing the panel returns it there.
                    cerrar(true);
                    onAbrir(p.id);
                  }}
                  className="flex w-full items-center gap-3 rounded-lg px-3 py-3 text-left text-[14.5px] font-semibold text-ink-soft hover:bg-line/60 hover:text-primary-dark focus:bg-line/60 focus:outline-hidden sm:py-2 sm:text-[13.5px]"
                >
                  <Icono size={18} aria-hidden />
                  <span className="flex-1">
                    {p.etiqueta}
                    <span className="block text-[12px] font-normal text-muted">{p.titulo}</span>
                  </span>
                  {insignia && (
                    <span className="rounded-pill bg-risk-high-fg px-1.5 text-[11px] font-bold leading-[18px] text-white">
                      {insignia.valor}
                      <span className="sr-only"> — {insignia.etiqueta}</span>
                    </span>
                  )}
                </button>
              );
            })}
          </div>
        </>
      )}
    </div>
  );
}
