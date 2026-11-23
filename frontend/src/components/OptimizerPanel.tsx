// «Optimizar mis impuestos»: the best COMBINATION of the choices the law leaves to a household
// (filing jointly or not, pension contributions, the donation and the latent losses), from the
// deterministic optimiser behind POST /optimize — no model call. Every figure on this screen is
// the plan's; the screen never adds standalone savings up, and says which savings are deferrals.
//
// Two tabs: «Tus datos» (figures — from the case or typed —, the spouse's figures and the user's
// constraints) and «Tu plan». A plan computed from other inputs than the ones on screen is marked
// as outdated until it is recalculated.
//
// Everything on «Tus datos» starts from the case: the user's own facts, the spouse's figures
// (fact `datos_conyuge`) and the constraints (fact `preferencias_optimizacion`); what the user types
// is layered over them until «Guardar en mi expediente» writes it. The request always carries the
// screen's spouse figures and constraints in full, so the server's stored defaults never add
// something the screen does not show.
//
// Shown as a page (registro.ts: presentacion "pagina"): the shell puts the title and the frame.

import { useRef } from "react";

import type { Fact } from "../api";
import { AvisoError, Label, VisuallyHidden } from "../ui";
import { CheckIcon } from "../ui/iconos";
import { Pestanas } from "./calculos/Pestanas";
import { OptimizadorDatos } from "./optimizador/OptimizadorDatos";
import { OptimizadorPlan } from "./optimizador/OptimizadorPlan";
import { useOptimizador } from "./optimizador/useOptimizador";
import type { SimulatorSeed } from "./SimulatorPanel";

interface OptimizerPanelProps {
  caseId: string | null;
  facts: Fact[];
  taxPeriod: string | null;
  /** Persist one fact to the case (null deletes it). Resolves true when saved. */
  onSaveFact?: (factName: string, value: string | number | boolean | null) => Promise<boolean>;
  busy?: boolean;
  /** Open the simulator (for data this screen does not ask, such as the detail of each child). */
  onOpenSimulator: () => void;
  /** Open the simulator with the plan's figures. */
  onUseInSimulator?: (seed: SimulatorSeed) => void;
}

export function OptimizerPanel({ caseId, facts, taxPeriod, onSaveFact, busy = false, onOpenSimulator, onUseInSimulator }: OptimizerPanelProps) {
  // Always mounted while the panel host keeps it: what was typed survives closing and reopening.
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const headlineRef = useRef<HTMLElement | null>(null);
  const o = useOptimizador({ caseId, facts, taxPeriod, onSaveFact, onOpenSimulator, onUseInSimulator, scrollRef, headlineRef });
  const { state, stale, year, acciones } = o;
  const { tab, computed, loading, error } = state;

  return (
    <div className="flex flex-col gap-4">
      <p className="m-0 text-sm text-fg-muted">Para tu IRPF de {computed?.plan.ejercicio ?? year}.</p>
      <Pestanas
        etiqueta="Optimizador"
        idBase="opt"
        actual={tab}
        onCambiar={acciones.showTab}
        pestanas={[
          { id: "datos", etiqueta: "1. Tus datos" },
          {
            id: "plan",
            etiqueta: "2. Tu plan",
            extra:
              computed && !stale ? (
                <>
                  <CheckIcon className="text-fg-success" />
                  <VisuallyHidden>(calculado)</VisuallyHidden>
                </>
              ) : stale ? (
                <Label variant="attention" size="small">
                  desactualizado
                </Label>
              ) : null,
          },
        ]}
      />

      <div ref={scrollRef} role="tabpanel" id="opt-tabpanel" aria-labelledby={`opt-tab-${tab}`} className="flex flex-col gap-4">
        {error && tab === "plan" && (
          <div id="opt-error">
            <AvisoError frase="No he podido calcular tu plan." detalle={error} onReintentar={() => void o.acciones.calcular()}>
              {computed && <p className="m-0 text-xs">El plan de la pestaña «Tu plan» es el del último cálculo que salió bien.</p>}
            </AvisoError>
          </div>
        )}
        {loading && (
          <VisuallyHidden role="status">Calculando la mejor combinación…</VisuallyHidden>
        )}
        <div key={tab}>
          {tab === "datos" ? (
            <OptimizadorDatos o={o} puedeGuardar={Boolean(caseId && onSaveFact)} busy={busy} onOpenSimulator={onOpenSimulator} />
          ) : (
            <OptimizadorPlan o={o} caseId={caseId} puedeUsarEnSimulador={Boolean(onUseInSimulator)} headlineRef={headlineRef} />
          )}
        </div>
      </div>

      <p className="m-0 text-xs text-fg-muted">Calculado con fórmulas fijas, sin inteligencia artificial. No sustituye al borrador de la Agencia Tributaria.</p>
    </div>
  );
}
