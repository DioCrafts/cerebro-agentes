/* ui-legado: pendiente de migrar a src/ui (docs/ui/sistema-de-diseno.md) */
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

import { Check, PiggyBank, TriangleAlert, X } from "lucide-react";
import clsx from "clsx";
import { useEffect, useRef } from "react";

import type { Fact } from "../api";
import { OptimizadorDatos } from "./optimizador/OptimizadorDatos";
import { OptimizadorPlan } from "./optimizador/OptimizadorPlan";
import { type Tab, useOptimizador } from "./optimizador/useOptimizador";
import type { SimulatorSeed } from "./SimulatorPanel";

interface OptimizerPanelProps {
  open: boolean;
  onClose: () => void;
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

export function OptimizerPanel({ open, onClose, caseId, facts, taxPeriod, onSaveFact, busy = false, onOpenSimulator, onUseInSimulator }: OptimizerPanelProps) {
  // Always mounted while the panel host keeps it: what was typed survives closing and reopening.
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const headlineRef = useRef<HTMLElement | null>(null);
  const o = useOptimizador({ caseId, facts, taxPeriod, onSaveFact, onOpenSimulator, onUseInSimulator, scrollRef, headlineRef });
  const { state, stale, year, acciones } = o;
  const { tab, computed, loading, error } = state;

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-40 flex justify-end">
      <button type="button" aria-label="Cerrar el optimizador" className="absolute inset-0 bg-ink/40" onClick={onClose} />
      <aside role="dialog" aria-modal="true" aria-labelledby="opt-title" className="relative flex h-full w-full max-w-[560px] flex-col border-l border-line bg-paper shadow-pop">
        <header className="border-b border-line px-4 pt-4 sm:px-5">
          <div className="flex items-start justify-between gap-2">
            <div className="min-w-0">
              <h2 id="opt-title" className="flex items-center gap-2 font-serif text-[19px] font-bold text-ink">
                <PiggyBank size={18} aria-hidden className="shrink-0 text-primary-dark" />
                Optimizar mis impuestos
              </h2>
              <p className="text-[12.5px] leading-snug text-muted">La mejor combinación de lo que la ley te deja elegir, para tu IRPF de {computed?.plan.ejercicio ?? year}</p>
            </div>
            <button type="button" onClick={onClose} aria-label="Cerrar" className="rounded-lg p-2 text-muted hover:bg-line/60 hover:text-ink">
              <X size={18} />
            </button>
          </div>
          <div role="tablist" aria-label="Optimizador" className="mt-3 flex gap-1">
            {(
              [
                ["datos", "1. Tus datos"],
                ["plan", "2. Tu plan"],
              ] as Array<[Tab, string]>
            ).map(([id, label]) => (
              <button
                key={id}
                type="button"
                role="tab"
                id={`opt-tab-${id}`}
                aria-selected={tab === id}
                aria-controls="opt-tabpanel"
                onClick={() => acciones.showTab(id)}
                onKeyDown={(e) => {
                  if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
                    const next: Tab = id === "datos" ? "plan" : "datos";
                    acciones.showTab(next);
                    document.getElementById(`opt-tab-${next}`)?.focus();
                  }
                }}
                tabIndex={tab === id ? 0 : -1}
                className={clsx(
                  "-mb-px inline-flex items-center gap-1.5 whitespace-nowrap border-b-2 px-3 py-2 text-[13px] font-semibold transition",
                  tab === id ? "border-primary text-primary-dark" : "border-transparent text-ink-soft hover:text-ink",
                )}
              >
                {label}
                {id === "plan" && computed && !stale && (
                  <>
                    <Check size={13} aria-hidden className="text-grounding-cited-fg" />
                    <span className="sr-only">(calculado)</span>
                  </>
                )}
                {id === "plan" && stale && <span className="rounded-pill bg-grounding-uncited-bg px-1.5 text-[11px] font-semibold text-grounding-uncited-fg">desactualizado</span>}
              </button>
            ))}
          </div>
        </header>

        <div ref={scrollRef} role="tabpanel" id="opt-tabpanel" aria-labelledby={`opt-tab-${tab}`} className="flex-1 overflow-y-auto overflow-x-hidden px-4 pt-4 sm:px-5">
          {error && tab === "plan" && (
            <p id="opt-error" role="alert" className="mb-4 flex gap-2 rounded-lg border border-risk-high-fg/30 bg-risk-high-bg px-3 py-2 text-[13px] text-ink-soft">
              <TriangleAlert size={15} aria-hidden className="mt-0.5 shrink-0 text-risk-high-fg" />
              <span>
                {error}
                {computed && <span className="block text-[12px] text-muted">El plan de la pestaña «Tu plan» es el del último cálculo que salió bien.</span>}
              </span>
            </p>
          )}
          {loading && (
            <p role="status" className="sr-only">
              Calculando la mejor combinación…
            </p>
          )}
          <div key={tab} className={clsx(tab === "plan" && "pb-6")}>
            {tab === "datos" ? (
                <OptimizadorDatos o={o} puedeGuardar={Boolean(caseId && onSaveFact)} busy={busy} onOpenSimulator={onOpenSimulator} />
              ) : (
                <OptimizadorPlan o={o} caseId={caseId} puedeUsarEnSimulador={Boolean(onUseInSimulator)} headlineRef={headlineRef} />
              )}
          </div>
        </div>

        <footer className="border-t border-line px-4 py-2.5 text-[11.5px] leading-snug text-muted sm:px-5">
          Cálculo determinista, sin inteligencia artificial. No sustituye al borrador de la Agencia Tributaria.
        </footer>
      </aside>
    </div>
  );
}
