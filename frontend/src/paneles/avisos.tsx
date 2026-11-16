// «Avisos»: the advisor's notices for this case (NotificationsPanel), with «Lo que sé de tus preferencias» inside.

import { NotificationsPanel } from "../components/NotificationsPanel";
import type { PropsPanel } from "./tipos";

export default function PanelAvisos({ ctx, open, onClose }: PropsPanel) {
  return (
    <NotificationsPanel
      open={open}
      onClose={onClose}
      notifications={ctx.avisos.items}
      hasCase={ctx.caso.id !== null}
      loading={ctx.avisos.cargando}
      error={ctx.avisos.error}
      onMarkRead={ctx.avisos.marcarLeido}
      onRefresh={ctx.avisos.recargar}
      onAsk={ctx.preguntar}
      moduleNames={ctx.modulos.nombres}
      caseId={ctx.caso.id}
    />
  );
}
