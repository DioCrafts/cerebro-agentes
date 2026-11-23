// One lever of the plan: what to do, what it saves inside the plan and by itself, and its requirements.

import type { PalancaElegida } from "../../apiOptimizador";
import { NATURALEZA, compromisoLabel, eur, euroPorEuro, naturalezaTexto, titularTexto } from "../../lib/optimizador/textos";
import { formatDate } from "../../lib/presentation";
import { Caja, CounterLabel, Label } from "../../ui";
import { AlertIcon } from "../../ui/iconos";
import { Plegable } from "../calculos/piezas";
import { MemoriaBotones } from "../MemoriaBotones";
import { Quoted } from "./piezas";

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

function Cifra({ titulo, children, destacada = false, peligro = false }: { titulo: string; children: string; destacada?: boolean; peligro?: boolean }) {
  return (
    <div className="flex flex-col">
      <dt className="text-xs text-fg-muted">{titulo}</dt>
      <dd className={destacada ? "m-0 text-xl font-semibold tabular-nums text-fg-success" : peligro ? "m-0 font-semibold tabular-nums text-fg-danger" : "m-0 font-semibold tabular-nums"}>{children}</dd>
    </div>
  );
}

export function PalancaCard({ p, index, caseId, open, onToggle, onRecalcular }: Props) {
  const nat = NATURALEZA[p.naturaleza];
  const aislado = Number(p.ahorro_aislado);
  const compromiso = Number(p.compromiso);
  return (
    <Caja as="li" aria-labelledby={`palanca-${p.id}`}>
      <Caja.Cuerpo className="flex flex-col gap-3 text-sm">
        <div className="flex items-start gap-3">
          <CounterLabel scheme="primary" aria-hidden>
            {index + 1}
          </CounterLabel>
          <div className="flex min-w-0 flex-1 flex-col gap-1">
            <p className="m-0 text-xs text-fg-muted">
              {p.nombre}
              {p.titular !== "hogar" && <> · {titularTexto(p.titular)}</>}
            </p>
            <h4 id={`palanca-${p.id}`} className="m-0 text-base font-semibold">
              {p.accion}
            </h4>
            {(p.plazo || p.id === "tributacion") && (
              <p className="m-0 text-fg-muted">{p.plazo ? <>Fecha límite: {formatDate(p.plazo)}</> : "Se elige al presentar la declaración"}</p>
            )}
            <div className="flex flex-wrap gap-1">
              <Label variant={nat.variante}>{nat.label}</Label>
              {!p.verificado && (
                <Label variant="danger">
                  <AlertIcon size={12} className="mr-1" />
                  No verificado
                </Label>
              )}
            </div>
          </div>
        </div>
        <dl className="m-0 grid grid-cols-2 gap-x-4 gap-y-3 border-t border-border-muted pt-3 sm:grid-cols-3">
          <Cifra titulo="Ahorro dentro del plan" destacada>
            {eur(p.ahorro_marginal)}
          </Cifra>
          <Cifra titulo="Por sí sola" peligro={aislado < 0}>
            {aislado > 0 ? eur(aislado) : aislado < 0 ? `costaría ${eur(-aislado)} más` : "no ahorraría nada"}
          </Cifra>
          {compromiso > 0 && <Cifra titulo={compromisoLabel(p)}>{eur(compromiso)}</Cifra>}
          {p.ahorro_por_euro !== null && <Cifra titulo="Ahorro por euro comprometido">{euroPorEuro(p.ahorro_por_euro)}</Cifra>}
          {p.coste_neto !== null && <Cifra titulo="Coste neto (lo que te cuesta)">{eur(p.coste_neto)}</Cifra>}
        </dl>
        <p className="m-0">{naturalezaTexto(p)}</p>
        {aislado <= 0 && Number(p.ahorro_marginal) > 0 && <p className="m-0">Por sí sola no compensaría: solo ahorra combinada con el resto del plan.</p>}
        {p.accion_aislada !== p.accion && aislado > 0 && <p className="m-0">Por sí sola, lo mejor sería otra cosa: «{p.accion_aislada}».</p>}
        <Plegable abierto={open} onAlternar={onToggle} titulo={`Requisitos que debes comprobar (${p.requisitos.length})`}>
          <ul className="m-0 flex list-disc flex-col gap-1 pl-5">
            {p.requisitos.map((r, i) => (
              <li key={i}>
                <Quoted text={r} />
              </li>
            ))}
          </ul>
          <p className="m-0 mt-2 text-xs text-fg-muted">{p.explicacion}</p>
        </Plegable>
        <p className="m-0 text-xs text-fg-muted">
          <strong>Base legal:</strong> {p.referencia}
          {!p.verificado && " · cifras pendientes de contrastar con el BOE"}
        </p>
        {/* Remembered (asistente/nucleo/memoria): the plan is recomputed without what the user rejects. */}
        {caseId && <MemoriaBotones caseId={caseId} palanca={{ id: p.id, opcion: p.opcion, titulo: p.accion, importe: p.ahorro_marginal }} onDone={onRecalcular} />}
      </Caja.Cuerpo>
    </Caja>
  );
}
