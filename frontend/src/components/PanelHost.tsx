// Mounts the registry's panels (src/paneles/registro.ts) on demand.
//
// A panel's code is downloaded the first time it opens (React.lazy: one chunk per panel). Once opened it
// stays mounted while closed — exactly like before the registry, when every panel was always mounted — so
// what the user typed in it survives closing and reopening. While open: focus moves into the dialog if the
// panel did not put it there itself, and Tab stays inside it (the dialogs are `aria-modal`).

import { X } from "lucide-react";
import type { ComponentType, ErrorInfo, LazyExoticComponent, ReactNode, RefObject } from "react";
import { Component, Suspense, createElement, lazy, useEffect, useRef, useState } from "react";

import type { ContextoPanel, DefinicionPanel, PropsPanel } from "../paneles/tipos";

const ENFOCABLES =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

const perezosos = new Map<string, LazyExoticComponent<ComponentType<PropsPanel>>>();

function componenteDe(panel: DefinicionPanel): LazyExoticComponent<ComponentType<PropsPanel>> {
  let componente = perezosos.get(panel.id);
  if (!componente) {
    componente = lazy(panel.cargar);
    perezosos.set(panel.id, componente);
  }
  return componente;
}

function dialogoActivo(contenedor: HTMLElement | null): HTMLElement | null {
  const dialogos = contenedor?.querySelectorAll<HTMLElement>('[role="dialog"]');
  return dialogos && dialogos.length > 0 ? (dialogos[dialogos.length - 1] ?? null) : null;
}

function FocoEnDialogo({ open, contenedor }: { open: boolean; contenedor: RefObject<HTMLDivElement | null> }) {
  useEffect(() => {
    if (!open) return;
    const dialogo = dialogoActivo(contenedor.current);
    if (dialogo && !dialogo.contains(document.activeElement)) {
      const primero = dialogo.querySelector<HTMLElement>(ENFOCABLES);
      if (primero) primero.focus();
      else {
        dialogo.tabIndex = -1;
        dialogo.focus();
      }
    }
    const atrapar = (e: KeyboardEvent) => {
      if (e.key !== "Tab") return;
      const actual = dialogoActivo(contenedor.current);
      if (!actual) return;
      const enfocables = Array.from(actual.querySelectorAll<HTMLElement>(ENFOCABLES)).filter((el) => el.offsetParent !== null);
      if (enfocables.length === 0) return;
      const primero = enfocables[0];
      const ultimo = enfocables.at(-1);
      if (!primero || !ultimo) return;
      if (!actual.contains(document.activeElement)) {
        e.preventDefault();
        primero.focus();
      } else if (e.shiftKey && document.activeElement === primero) {
        e.preventDefault();
        ultimo.focus();
      } else if (!e.shiftKey && document.activeElement === ultimo) {
        e.preventDefault();
        primero.focus();
      }
    };
    document.addEventListener("keydown", atrapar);
    return () => document.removeEventListener("keydown", atrapar);
  }, [open, contenedor]);
  return null;
}

function Cargando({ onClose }: { onClose: () => void }) {
  return (
    <div className="fixed inset-0 z-40 flex justify-end">
      <button type="button" aria-label="Cerrar" className="absolute inset-0 bg-ink/40" onClick={onClose} />
      <div role="dialog" aria-modal="true" aria-label="Cargando" className="relative flex h-full w-full max-w-[440px] items-center justify-center border-l border-line bg-paper shadow-pop">
        <p role="status" className="text-[13.5px] text-muted">
          Cargando…
        </p>
      </div>
    </div>
  );
}

interface FalloProps {
  open: boolean;
  onClose: () => void;
  children: ReactNode;
}

/** A panel whose code cannot be downloaded (offline before it was ever opened, or a new deploy). */
class FalloDeCarga extends Component<FalloProps, { fallo: boolean }> {
  state = { fallo: false };

  static getDerivedStateFromError() {
    return { fallo: true };
  }

  componentDidCatch(error: unknown, info: ErrorInfo) {
    console.error("panel failed to load", error, info.componentStack);
  }

  render() {
    if (!this.state.fallo) return this.props.children;
    if (!this.props.open) return null;
    return (
      <div className="fixed inset-0 z-40 flex justify-end">
        <button type="button" aria-label="Cerrar" className="absolute inset-0 bg-ink/40" onClick={this.props.onClose} />
        <div role="dialog" aria-modal="true" aria-label="No se pudo abrir" className="relative flex h-full w-full max-w-[440px] flex-col border-l border-line bg-paper p-5 shadow-pop">
          <div className="flex items-center justify-between">
            <h2 className="font-serif text-[19px] font-bold text-ink">No se pudo abrir</h2>
            <button type="button" onClick={this.props.onClose} aria-label="Cerrar" className="rounded-lg p-2 text-muted hover:bg-line/60 hover:text-ink">
              <X size={18} />
            </button>
          </div>
          <p role="alert" className="mt-3 text-[13.5px] leading-relaxed text-ink-soft">
            No se ha podido cargar esta parte de la aplicación. Comprueba tu conexión y vuelve a intentarlo; si acabas de
            recibir una versión nueva, recarga la página.
          </p>
          <button
            type="button"
            onClick={() => window.location.reload()}
            className="mt-4 self-start rounded-lg bg-primary px-3 py-2 text-[13.5px] font-semibold text-white hover:bg-primary-hover"
          >
            Recargar
          </button>
        </div>
      </div>
    );
  }
}

function Ranura({ panel, open, ctx, onClose }: { panel: DefinicionPanel; open: boolean; ctx: ContextoPanel; onClose: () => void }) {
  const contenedor = useRef<HTMLDivElement | null>(null);
  // The lazy component is cached per panel id (componenteDe), so it is the same on every render.
  const props: PropsPanel = { ctx, open, onClose };
  return (
    <div ref={contenedor} className="contents" data-ranura={panel.id}>
      <FalloDeCarga open={open} onClose={onClose}>
        <Suspense fallback={open ? <Cargando onClose={onClose} /> : null}>
          {createElement(componenteDe(panel), props)}
          <FocoEnDialogo open={open} contenedor={contenedor} />
        </Suspense>
      </FalloDeCarga>
    </div>
  );
}

interface PanelHostProps {
  /** The panels offered right now (one that stops being offered is unmounted). */
  paneles: DefinicionPanel[];
  abierto: string | null;
  ctx: ContextoPanel;
  onClose: () => void;
}

export function PanelHost({ paneles, abierto, ctx, onClose }: PanelHostProps) {
  // The panels opened at least once (in order of first opening).
  const [montados, setMontados] = useState<string[]>([]);
  if (abierto !== null && !montados.includes(abierto) && paneles.some((p) => p.id === abierto)) {
    setMontados([...montados, abierto]);
  }
  return (
    <>
      {montados.map((id) => {
        const panel = paneles.find((p) => p.id === id);
        return panel ? <Ranura key={id} panel={panel} open={abierto === id} ctx={ctx} onClose={onClose} /> : null;
      })}
    </>
  );
}
