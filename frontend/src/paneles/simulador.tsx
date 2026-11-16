// «Simulador de IRPF». Keyed by conversation (and by the plan the optimiser handed over): figures typed for
// one consultation never leak into another.

import { SimulatorPanel } from "../components/SimulatorPanel";
import type { PropsPanel } from "./tipos";

export default function PanelSimulador({ ctx, open, onClose }: PropsPanel) {
  const semilla = ctx.semillaSimulador;
  return (
    <SimulatorPanel
      key={semilla ? `${ctx.conversacionId}-plan-${semilla.id}` : ctx.conversacionId}
      seed={semilla ? semilla.seed : null}
      open={open}
      onClose={onClose}
      caseId={ctx.caso.id}
      facts={ctx.caso.facts}
      taxPeriod={ctx.caso.taxPeriod}
      onSaveFact={ctx.cambiarDato}
      busy={ctx.ocupado}
    />
  );
}
