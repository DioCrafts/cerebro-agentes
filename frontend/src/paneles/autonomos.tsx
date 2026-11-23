// «Autónomos»: models 130/303 and the activity's figures, shown as a page.

import { AutonomosPanel } from "../components/AutonomosPanel";
import type { PropsPanel } from "./tipos";

export default function PanelAutonomos({ ctx, open }: PropsPanel) {
  return (
    <AutonomosPanel
      key={`autonomos-${ctx.conversacionId}`}
      open={open}
      taxPeriod={ctx.caso.taxPeriod}
      caseId={ctx.caso.id}
      facts={ctx.caso.facts}
      onSaveFact={ctx.cambiarDato}
      busy={ctx.ocupado}
      // The panel has saved the income and expenses as facts of the case; the simulator works the
      // activity's net income out of them together with the rest of the case.
      onUse={() => ctx.abrir("simulador")}
    />
  );
}
