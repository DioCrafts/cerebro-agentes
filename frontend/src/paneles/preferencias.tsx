// «Lo que sé de tus preferencias» (also reachable from «Avisos»): its own entry so it can be linked to.
// Shown in a side sheet (registro.ts).

import { PreferenciasPanel } from "../components/PreferenciasPanel";
import type { PropsPanel } from "./tipos";

export default function PanelPreferencias({ ctx, open }: PropsPanel) {
  return <PreferenciasPanel open={open} caseId={ctx.caso.id} onChanged={ctx.avisos.recargar} />;
}
