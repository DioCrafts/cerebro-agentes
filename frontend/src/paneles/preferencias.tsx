// «Lo que sé de tus preferencias» (also reachable from «Avisos»): its own entry so it can be linked to.

import { PreferenciasPanel } from "../components/PreferenciasPanel";
import type { PropsPanel } from "./tipos";

export default function PanelPreferencias({ ctx, open, onClose }: PropsPanel) {
  return <PreferenciasPanel open={open} onClose={onClose} caseId={ctx.caso.id} onChanged={ctx.avisos.recargar} />;
}
