// One lever of the plan: what to do, what it saves inside the plan and by itself, and its requirements.

import { ChevronDown, CircleAlert } from "lucide-react";
import clsx from "clsx";

import type { PalancaElegida } from "../../apiOptimizador";
import { NATURALEZA, compromisoLabel, eur, euroPorEuro, naturalezaTexto, titularTexto } from "../../lib/optimizador/textos";
import { formatDate } from "../../lib/presentation";
import { MemoriaBotones } from "../MemoriaBotones";
import { Badge, Quoted } from "./piezas";

interface Props {
  p: PalancaElegida;
  index: number;
  caseId: string | null;
  /** Whether the requirements are shown. */
  open: boolean;
  onToggle: () => void;
  /** Recompute the plan (after the user rejects or accepts a lever). */
  onRecalcular: () => void;
}

export function PalancaCard({ p, index, caseId, open, onToggle, onRecalcular }: Props) {
  const nat = NATURALEZA[p.naturaleza];
  const aislado = Number(p.ahorro_aislado);
  const compromiso = Number(p.compromiso);
    return (
    <li className="rounded-xl border border-line bg-surface px-4 py-3.5 shadow-card">
      <div className="flex items-start gap-3">
        <span aria-hidden className="mt-0.5 flex h-6 w-6 flex-shrink-0 items-center justify-center rounded-full bg-primary-soft text-[12.5px] font-bold text-primary-dark">
          {index + 1}
        </span>
        <div className="min-w-0 flex-1">
          <p className="text-[12px] text-muted">
            {p.nombre}
            {p.titular !== "hogar" && <> · {titularTexto(p.titular)}</>}
          </p>
          <h4 className="text-[14.5px] font-semibold leading-snug text-ink">{p.accion}</h4>
          <p className="mt-0.5 text-[12.5px] text-ink-soft">{p.plazo ? <>Fecha límite: {formatDate(p.plazo)}</> : p.id === "tributacion" ? "Se elige al presentar la declaración" : null}</p>
          <div className="mt-2 flex flex-wrap gap-1.5">
            <Badge className={nat.cls}>{nat.label}</Badge>
            {!p.verificado && (
              <Badge className="border-risk-high-fg/30 bg-risk-high-bg text-risk-high-fg">
                <CircleAlert size={12} aria-hidden />
                No verificado
              </Badge>
            )}
          </div>
        </div>
      </div>
      <dl className="mt-3 grid grid-cols-2 gap-x-3 gap-y-2.5 border-t border-line pt-3 sm:grid-cols-3">
        <div>
          <dt className="text-[11.5px] leading-tight text-muted">Ahorro dentro del plan</dt>
          <dd className="mt-0.5 font-serif text-[18px] font-bold leading-tight text-grounding-cited-fg">{eur(p.ahorro_marginal)}</dd>
        </div>
        <div>
          <dt className="text-[11.5px] leading-tight text-muted">Por sí sola</dt>
          <dd className={clsx("mt-0.5 text-[13.5px] font-semibold leading-tight", aislado < 0 ? "text-risk-high-fg" : "text-ink")}>
            {aislado > 0 ? eur(aislado) : aislado < 0 ? `costaría ${eur(-aislado)} más` : "no ahorraría nada"}
          </dd>
        </div>
        {compromiso > 0 && (
          <div>
            <dt className="text-[11.5px] leading-tight text-muted">{compromisoLabel(p)}</dt>
            <dd className="mt-0.5 text-[13.5px] font-semibold leading-tight text-ink">{eur(compromiso)}</dd>
          </div>
        )}
        {p.ahorro_por_euro !== null && (
          <div>
            <dt className="text-[11.5px] leading-tight text-muted">Ahorro por euro comprometido</dt>
            <dd className="mt-0.5 text-[13.5px] font-semibold leading-tight text-ink">{euroPorEuro(p.ahorro_por_euro)}</dd>
          </div>
        )}
        {p.coste_neto !== null && (
          <div>
            <dt className="text-[11.5px] leading-tight text-muted">Coste neto (lo que te cuesta)</dt>
            <dd className="mt-0.5 text-[13.5px] font-semibold leading-tight text-ink">{eur(p.coste_neto)}</dd>
          </div>
        )}
      </dl>
      <p className="mt-2.5 text-[12.5px] leading-snug text-ink-soft">{naturalezaTexto(p)}</p>
      {aislado <= 0 && Number(p.ahorro_marginal) > 0 && (
        <p className="mt-1.5 text-[12.5px] leading-snug text-ink-soft">Por sí sola no compensaría: solo ahorra combinada con el resto del plan.</p>
      )}
      {p.accion_aislada !== p.accion && aislado > 0 && (
        <p className="mt-1.5 text-[12.5px] leading-snug text-ink-soft">Por sí sola, lo mejor sería otra cosa: «{p.accion_aislada}».</p>
      )}
      <details open={open} className="group mt-2.5">
        <summary
          onClick={(e) => {
            e.preventDefault();
            onToggle();
          }}
          className="flex cursor-pointer list-none items-center gap-1 text-[12.5px] font-semibold text-primary-dark [&::-webkit-details-marker]:hidden"
        >
          <ChevronDown size={14} aria-hidden className="transition group-open:rotate-180" />
          Requisitos que debes comprobar ({p.requisitos.length})
        </summary>
        <ul className="mt-2 list-disc space-y-1.5 pl-5 text-[12.5px] leading-snug text-ink-soft">
          {p.requisitos.map((r, i) => (
            <li key={i}>
              <Quoted text={r} />
            </li>
          ))}
        </ul>
        <p className="mt-2 text-[12px] leading-snug text-muted">{p.explicacion}</p>
      </details>
      <p className="mt-2 text-[11.5px] leading-snug text-muted">
        <span className="font-semibold">Base legal:</span> {p.referencia}
        {!p.verificado && " · cifras pendientes de contrastar con el BOE"}
      </p>
      {/* Remembered (asistente/nucleo/memoria): the plan is recomputed without what the user rejects. */}
      {caseId && (
        <MemoriaBotones
          caseId={caseId}
          palanca={{ id: p.id, opcion: p.opcion, titulo: p.accion, importe: p.ahorro_marginal }}
          onDone={onRecalcular}
        />
      )}
    </li>
  );
}
