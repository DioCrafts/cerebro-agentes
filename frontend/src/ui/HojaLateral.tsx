// HojaLateral: a modal side sheet (Primer's Dialog with position="right"), with one difference: it can stay
// mounted while closed (`hidden`), so what was typed in it survives closing and reopening.
//
// Accessibility: role="dialog", aria-modal, named by its title; focus is trapped inside while open (Primer's
// useFocusTrap) and Escape closes it (Primer's useOnEscapePress, which lets an open menu inside close first).
// Focus goes back to whatever opened it: the caller's job (the shell does it for every panel).
//
//   <HojaLateral open={open} onClose={onClose} titulo="Avisos de tu asesor" icono={BellIcon} subtitulo="…">
//     …
//   </HojaLateral>

import type { Icon } from "@primer/octicons-react";
import { XIcon } from "@primer/octicons-react";
import { IconButton, Portal, useFocusTrap, useOnEscapePress } from "@primer/react";
import { clsx } from "clsx";
import type { ReactNode } from "react";
import { useEffect, useId, useRef } from "react";

import estilos from "./HojaLateral.module.css";

export interface HojaLateralProps {
  open: boolean;
  onClose: () => void;
  titulo: ReactNode;
  subtitulo?: ReactNode;
  icono?: Icon;
  /** Buttons next to the close button (IconButton with aria-label). */
  acciones?: ReactNode;
  pie?: ReactNode;
  /** 480px (default), 640px or 960px on a computer; always the full width on a phone. */
  ancho?: "medium" | "large" | "xlarge";
  /** Accessible name of the close button. */
  etiquetaCerrar?: string;
  children: ReactNode;
}

export function HojaLateral({ open, onClose, titulo, subtitulo, icono: Icono, acciones, pie, ancho = "medium", etiquetaCerrar = "Cerrar", children }: HojaLateralProps) {
  const idTitulo = useId();
  const idSubtitulo = useId();
  const contenedor = useRef<HTMLElement | null>(null);
  const tituloRef = useRef<HTMLHeadingElement | null>(null);
  useFocusTrap({ containerRef: contenedor, initialFocusRef: tituloRef, disabled: !open });
  useOnEscapePress(
    (e) => {
      if (!open) return;
      e.preventDefault();
      onClose();
    },
    [open, onClose],
  );
  // The page behind does not scroll while the sheet is open.
  useEffect(() => {
    if (!open) return;
    const antes = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = antes;
    };
  }, [open]);

  return (
    <Portal>
      <div className={estilos.fondo} hidden={!open}>
        <div className={estilos.cerrarFondo} aria-hidden onClick={onClose} />
        <section
          ref={contenedor}
          role="dialog"
          aria-modal="true"
          aria-labelledby={idTitulo}
          aria-describedby={subtitulo ? idSubtitulo : undefined}
          className={clsx(estilos.hoja, estilos[ancho])}
        >
          {/* A div, not <header>: inside role="dialog" a header would be a second page banner. */}
          <div className={estilos.cabecera}>
            <div className={estilos.titulos}>
              <h2 id={idTitulo} ref={tituloRef} tabIndex={-1} className={estilos.titulo}>
                {Icono && <Icono size={16} />}
                {titulo}
              </h2>
              {subtitulo && (
                <p id={idSubtitulo} className={estilos.subtitulo}>
                  {subtitulo}
                </p>
              )}
            </div>
            <div className={estilos.acciones}>
              {acciones}
              <IconButton icon={XIcon} aria-label={etiquetaCerrar} variant="invisible" onClick={onClose} />
            </div>
          </div>
          <div className={estilos.cuerpo}>{children}</div>
          {pie && <footer className={estilos.pie}>{pie}</footer>}
        </section>
      </div>
    </Portal>
  );
}
