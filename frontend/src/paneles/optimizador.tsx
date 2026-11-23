// «Optimizar mis impuestos»: the year-end plan, shown as a page. It can hand its figures to the simulator.

import { OptimizerPanel } from "../components/OptimizerPanel";
import type { PropsPanel } from "./tipos";

export default function PanelOptimizador({ ctx }: PropsPanel) {
  return (
    <OptimizerPanel
      key={`optimizador-${ctx.conversacionId}`}
      caseId={ctx.caso.id}
      facts={ctx.caso.facts}
      taxPeriod={ctx.caso.taxPeriod}
      onSaveFact={ctx.cambiarDato}
      busy={ctx.ocupado}
      onOpenSimulator={() => ctx.abrir("simulador")}
      onUseInSimulator={ctx.usarEnSimulador}
    />
  );
}
