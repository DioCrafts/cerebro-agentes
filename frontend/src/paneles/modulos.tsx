// «Módulos del hogar»: switch modules on/off, their data and documents, the actions waiting for confirmation
// (and the Gmail section of the mail module).

import { ModulosPanel } from "../components/ModulosPanel";
import type { PropsPanel } from "./tipos";

export default function PanelModulos({ ctx, open, onClose }: PropsPanel) {
  return (
    <ModulosPanel
      open={open}
      onClose={onClose}
      modulos={ctx.modulos.datos}
      loading={ctx.modulos.datos === null && ctx.modulos.error === null}
      error={ctx.modulos.error}
      onToggle={ctx.modulos.cambiar}
      caseId={ctx.caso.id}
      facts={ctx.caso.facts}
      onCaseUpdated={(updated) => {
        ctx.aplicarCaso(updated);
        ctx.avisos.recargar();
      }}
      acciones={ctx.acciones.items}
      onDecide={ctx.acciones.decidir}
      disabled={ctx.ocupado}
    />
  );
}
