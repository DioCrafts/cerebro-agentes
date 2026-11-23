// Pieces of GitHub's settings pages that src/ui does not ship yet, used by the household and personal-data
// panels (módulos, expediente, preferencias, lo que sé de ti, avisos en el móvil):
//
//   ZonaPeligro / FilaPeligro — the «Danger zone» box: red border, one row per irreversible action, each with
//                               its explanation and a danger button on the right.
//   DialogoEleccion           — a Primer Dialog with more than two answers (e.g. «Desactivar y conservar» /
//                               «Desactivar y borrar»), with its close button in Spanish (Primer's says «Close»).
//   useConfirmar (useConfirmar.ts) — Primer's useConfirm with the buttons in Spanish and the danger style by default.
//
// Candidates to move to src/ui once the four migration groups are merged (docs/ui/sistema-de-diseno.md).

import type { ReactNode } from "react";

import { Caja, Dialog, IconButton } from "../ui";
import { XIcon } from "../ui/iconos";

export function ZonaPeligro({ titulo = "Zona de peligro", children }: { titulo?: string; children: ReactNode }) {
  return (
    <Caja as="section" variante="peligro" aria-label={titulo}>
      <Caja.Cabecera titulo={titulo} />
      <Caja.Lista>{children}</Caja.Lista>
    </Caja>
  );
}

export function FilaPeligro({ titulo, descripcion, accion }: { titulo: string; descripcion: ReactNode; accion: ReactNode }) {
  return (
    <Caja.Fila className="flex flex-wrap items-center gap-3">
      <div className="min-w-0 flex-1">
        <p className="m-0 font-semibold">{titulo}</p>
        <p className="m-0 text-xs text-fg-muted">{descripcion}</p>
      </div>
      <div className="shrink-0">{accion}</div>
    </Caja.Fila>
  );
}

export interface BotonDialogo {
  contenido: string;
  tipo?: "default" | "primary" | "danger";
  onClick: () => void;
  disabled?: boolean;
}

interface DialogoEleccionProps {
  titulo: string;
  onCerrar: () => void;
  botones: BotonDialogo[];
  children: ReactNode;
}

/** A dialog with several answers. Escape, the close button and «Cancelar» all close it without doing anything. */
export function DialogoEleccion({ titulo, onCerrar, botones, children }: DialogoEleccionProps) {
  return (
    <Dialog
      title={titulo}
      onClose={onCerrar}
      width="large"
      renderHeader={({ dialogLabelId }) => (
        <Dialog.Header className="flex items-start gap-2">
          <Dialog.Title id={dialogLabelId} className="flex-1">
            {titulo}
          </Dialog.Title>
          <IconButton icon={XIcon} aria-label="Cerrar" variant="invisible" size="small" onClick={onCerrar} />
        </Dialog.Header>
      )}
      footerButtons={[
        { content: "Cancelar", onClick: onCerrar },
        ...botones.map((b) => ({ content: b.contenido, buttonType: b.tipo ?? "default", onClick: b.onClick, disabled: b.disabled })),
      ]}
    >
      {children}
    </Dialog>
  );
}
