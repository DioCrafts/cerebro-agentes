// «Tu expediente»: what the advisor knows in this consultation, editable. Shown as a page (registro.ts).

import { CasePanel } from "../components/CasePanel";
import type { PropsPanel } from "./tipos";

export default function PanelExpediente({ ctx }: PropsPanel) {
  return (
    <CasePanel
      facts={ctx.caso.facts}
      taxPeriod={ctx.caso.taxPeriod}
      obligations={ctx.caso.obligations}
      caseId={ctx.caso.id}
      onChangeFact={ctx.cambiarDato}
      saving={ctx.guardandoDato}
      error={ctx.errorCaso}
      disabled={ctx.ocupado}
      excludeFacts={ctx.datosDeModulos}
    />
  );
}
