// «Tareas»: the long-running tasks (asistente/nucleo/tareas). Pending actions go through the shell's handler.

import { TareasPanel } from "../components/TareasPanel";
import type { PropsPanel } from "./tipos";

export default function PanelTareas({ ctx, open, onClose }: PropsPanel) {
  return (
    <TareasPanel
      open={open}
      onClose={onClose}
      caseId={ctx.caso.id}
      onAccion={ctx.acciones.decidir}
      onCambio={() => {
        ctx.avisos.recargar();
        ctx.acciones.recargar();
      }}
    />
  );
}
