/* ui-legado: pendiente de migrar a src/ui (docs/ui/sistema-de-diseno.md) */
// The latent positions of «Tus datos»: investments the user could sell before 31 December.

import { Info, Plus, Trash2 } from "lucide-react";
import clsx from "clsx";

import type { TipoActivo } from "../../apiOptimizador";
import type { Position } from "../../lib/optimizador/campos";
import { TIPO_ACTIVO, reglaRecompra } from "../../lib/optimizador/textos";
import { INPUT } from "./piezas";
import type { Optimizador } from "./useOptimizador";

export function EditorPosiciones({ o }: { o: Optimizador }) {
  const { positions } = o.vista;
  return (
  <fieldset className="min-w-0">
    <legend className="mb-2 text-[11px] font-semibold uppercase tracking-[0.12em] text-muted">Inversiones que podrías vender antes del 31 de diciembre</legend>
    <p className="text-[12.5px] leading-snug text-ink-soft">
      Una <strong className="font-semibold">pérdida latente</strong> es lo que perderías si vendieras hoy: venderla puede compensar tus ganancias del año. Las ganancias latentes no bajan el
      impuesto, pero el plan te avisa si realizarlas este año te sale gratis.
    </p>
    {positions.length > 0 && <ul className="mt-2.5 space-y-2.5">{positions.map((pos, index) => (
      <FilaPosicion key={pos.id} o={o} pos={pos} index={index} />
    ))}</ul>}
    <div className="mt-2.5 flex flex-wrap gap-2">
      <button
        type="button"
        onClick={() => o.acciones.addPosition("perdida")}
        disabled={positions.filter((p) => p.clase === "perdida").length >= 20}
        className="inline-flex items-center gap-1.5 rounded-lg border border-line px-2.5 py-1.5 text-[12.5px] font-semibold text-primary-dark transition hover:border-primary disabled:cursor-not-allowed disabled:opacity-50"
      >
        <Plus size={14} aria-hidden />
        Añadir una pérdida latente
      </button>
      <button
        type="button"
        onClick={() => o.acciones.addPosition("ganancia")}
        disabled={positions.filter((p) => p.clase === "ganancia").length >= 20}
        className="inline-flex items-center gap-1.5 rounded-lg border border-line px-2.5 py-1.5 text-[12.5px] font-semibold text-primary-dark transition hover:border-primary disabled:cursor-not-allowed disabled:opacity-50"
      >
        <Plus size={14} aria-hidden />
        Añadir una ganancia latente
      </button>
    </div>
  </fieldset>
  );
}

function FilaPosicion({ o, pos, index }: { o: Optimizador; pos: Position; index: number }) {
  const { positions } = o.vista;
  const { problems, spouseGiven } = o.peticion;
  const { setPosition, setPositions } = o.acciones;
  const base = `opt-pos-${pos.id}`;
  const n = positions.filter((p, i) => p.clase === pos.clase && i <= index).length;
  const name = `${pos.clase === "perdida" ? "Pérdida" : "Ganancia"} latente ${n}`;
  const importeProblem = problems[`${base}-importe`];
  const titularProblem = problems[`${base}-titular`];
  return (
    <li className="rounded-xl border border-line bg-surface px-3 py-3">
      <div className="mb-2 flex items-center justify-between gap-2">
        <p className={clsx("text-[13px] font-semibold", pos.clase === "perdida" ? "text-ink" : "text-ink-soft")}>{name}</p>
        <button
          type="button"
          onClick={() => setPositions((prev) => prev.filter((p) => p.id !== pos.id))}
          aria-label={`Quitar la ${name.toLowerCase()}`}
          title="Quitar"
          className="rounded-lg p-1.5 text-muted transition hover:bg-risk-high-bg hover:text-risk-high-fg"
        >
          <Trash2 size={15} aria-hidden />
        </button>
      </div>
      <div className="grid gap-2.5 sm:grid-cols-2">
        <div>
          <label htmlFor={`${base}-descripcion`} className="block text-[12px] leading-snug text-ink-soft">
            Qué es (opcional)
          </label>
          <input
            id={`${base}-descripcion`}
            value={pos.descripcion}
            maxLength={200}
            onChange={(e) => setPosition(pos.id, { descripcion: e.target.value })}
            placeholder="Fondo indexado, acciones de…"
            className={clsx(INPUT, "mt-0.5 border-line focus:border-primary")}
          />
        </div>
        <div>
          <label htmlFor={`${base}-importe`} className="block text-[12px] leading-snug text-ink-soft">
            {pos.clase === "perdida" ? "Pérdida si vendieras hoy" : "Ganancia si vendieras hoy"}
          </label>
          <span className="relative mt-0.5 block">
            <input
              id={`${base}-importe`}
              value={pos.importe}
              inputMode="decimal"
              placeholder="—"
              onChange={(e) => setPosition(pos.id, { importe: e.target.value })}
              aria-invalid={importeProblem ? true : undefined}
              aria-describedby={importeProblem ? `${base}-importe-problem` : `${base}-importe-hint`}
              className={clsx(INPUT, "pr-7", importeProblem ? "border-risk-high-fg" : "border-line focus:border-primary")}
            />
            <span className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-[13px] text-muted">€</span>
          </span>
          {importeProblem ? (
            <span id={`${base}-importe-problem`} className="mt-1 block text-[11.5px] font-semibold leading-snug text-risk-high-fg">
              {importeProblem}
            </span>
          ) : (
            <span id={`${base}-importe-hint`} className="mt-1 block text-[11.5px] leading-snug text-muted">
              Valor de venta menos lo que te costó, gastos incluidos. En positivo.
            </span>
          )}
        </div>
        <div>
          <label htmlFor={`${base}-tipo`} className="block text-[12px] leading-snug text-ink-soft">
            ¿Es un valor cotizado?
          </label>
          <select id={`${base}-tipo`} value={pos.tipo} onChange={(e) => setPosition(pos.id, { tipo: e.target.value as TipoActivo })} className={clsx(INPUT, "mt-0.5 border-line focus:border-primary")}>
            {TIPO_ACTIVO.map((t) => (
              <option key={t.value} value={t.value}>
                {t.label}
              </option>
            ))}
          </select>
        </div>
        {(spouseGiven || pos.titular === "conyuge") && (
          <div>
            <label htmlFor={`${base}-titular`} className="block text-[12px] leading-snug text-ink-soft">
              De quién es
            </label>
            <select
              id={`${base}-titular`}
              value={pos.titular}
              onChange={(e) => setPosition(pos.id, { titular: e.target.value === "conyuge" ? "conyuge" : "contribuyente" })}
              aria-invalid={titularProblem ? true : undefined}
              className={clsx(INPUT, "mt-0.5", titularProblem ? "border-risk-high-fg" : "border-line focus:border-primary")}
            >
              <option value="contribuyente">Mía</option>
              <option value="conyuge">De mi cónyuge</option>
            </select>
            {titularProblem && <span className="mt-1 block text-[11.5px] font-semibold leading-snug text-risk-high-fg">{titularProblem}</span>}
          </div>
        )}
      </div>
      {pos.clase === "perdida" && (
        <p className="mt-2.5 flex gap-1.5 rounded-lg bg-grounding-uncited-bg px-2.5 py-2 text-[12px] leading-snug text-ink-soft">
          <Info size={14} aria-hidden className="mt-0.5 shrink-0 text-grounding-uncited-fg" />
          <span>{reglaRecompra(pos.tipo)}</span>
        </p>
      )}
    </li>
  );
}

