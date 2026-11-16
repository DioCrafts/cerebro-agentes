// «Planificar el dinero del hogar»: the household planner.

import { PlanificadorPanel } from "../components/PlanificadorPanel";
import type { PropsPanel } from "./tipos";

export default function PanelPlanificar({ ctx, open, onClose }: PropsPanel) {
  return (
    <PlanificadorPanel
      key={`planificador-${ctx.conversacionId}`}
      open={open}
      onClose={onClose}
      caseId={ctx.caso.id}
      taxPeriod={ctx.caso.taxPeriod}
    />
  );
}
