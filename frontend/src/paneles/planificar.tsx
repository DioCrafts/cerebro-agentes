// «Planificar el dinero del hogar»: the household planner, shown as a page.

import { PlanificadorPanel } from "../components/PlanificadorPanel";
import type { PropsPanel } from "./tipos";

export default function PanelPlanificar({ ctx }: PropsPanel) {
  return <PlanificadorPanel key={`planificador-${ctx.conversacionId}`} caseId={ctx.caso.id} taxPeriod={ctx.caso.taxPeriod} />;
}
