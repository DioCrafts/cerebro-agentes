// «Avisos»: the advisor's notices for this case (NotificationsPanel). Shown as a side sheet (registro.ts);
// «Tus preferencias» opens the preferences panel.

import { NotificationsPanel } from "../components/NotificationsPanel";
import type { PropsPanel } from "./tipos";

export default function PanelAvisos({ ctx }: PropsPanel) {
  return (
    <NotificationsPanel
      notifications={ctx.avisos.items}
      hasCase={ctx.caso.id !== null}
      loading={ctx.avisos.cargando}
      error={ctx.avisos.error}
      onMarkRead={ctx.avisos.marcarLeido}
      onRefresh={ctx.avisos.recargar}
      onAsk={ctx.preguntar}
      onOpenPreferences={() => ctx.abrir("preferencias")}
      moduleNames={ctx.modulos.nombres}
      caseId={ctx.caso.id}
    />
  );
}
