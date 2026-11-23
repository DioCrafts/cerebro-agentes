// Simulator: change a figure, see the tax change. Everything here is the
// deterministic calculator (POST /simulate) — no model call, so it answers as
// you type. The saved case is the baseline; edits are compared against it.

import { Calculator, ChevronDown, Lightbulb, RotateCcw, Save, TriangleAlert, X } from "lucide-react";
import clsx from "clsx";
import { useEffect } from "react";

import type { Benefit, Fact } from "../api";
import { ObligacionDeclararCard } from "./ObligacionDeclararCard";
import { parseAmount } from "../lib/formulario";
import type { ObligacionDeclarar } from "../lib/obligacion";
import { COMUNIDADES, formatMoney } from "../lib/presentation";
import { FIELDS, type FieldDef, LEVER_PRESET, TRIBUTACION, gruposDeComunidad } from "../lib/simulador/campos";
import { EditorDescendientes } from "./simulador/EditorDescendientes";
import { type SimulatorSeed, useSimulacion } from "./simulador/useSimulacion";

export type { SimulatorSeed };

interface SimulatorPanelProps {
  open: boolean;
  onClose: () => void;
  caseId: string | null;
  facts: Fact[];
  taxPeriod: string | null;
  // Persist edited figures to the case (null removes the fact). Resolves true when saved.
  onSaveFact: (factName: string, value: number | string | boolean | null) => Promise<boolean>;
  busy: boolean;
  seed?: SimulatorSeed | null;
}
const INPUT_CLASS = "w-full rounded-lg border bg-surface px-3 py-2 text-[16px] text-ink focus:outline-none sm:text-[14px]";

export function SimulatorPanel({ open, onClose, caseId, facts, taxPeriod, onSaveFact, busy, seed = null }: SimulatorPanelProps) {
  const s = useSimulacion({ open, caseId, facts, taxPeriod, onSaveFact, seed });
  const { state, acciones, baseline, derived, value, partsSum, totalProblem, invalid, cambios, changed, comunidad, year } = s;
  const { edits, tributacion, childrenEdit, openParts, simulation, error, saving, saveError } = state;
  const groups = gruposDeComunidad(comunidad);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  const base = simulation?.escenarios[0];
  const sim = simulation?.escenarios[1] ?? base;
  const resultado = sim ? Number(sim.resultado.resultado) : 0;
  const ahorro = simulation?.escenarios[1] ? Number(simulation.escenarios[1].ahorro_frente_a_base) : 0;
  const levers = (simulation?.beneficios ?? []).filter((b) => b.estado === "disponible" || b.estado === "por_confirmar");
  // Whether the user must file (art. 96 LIRPF): computed by the backend with each individual result.
  const obligacion = sim ? ((sim.resultado as { obligacion_declarar?: ObligacionDeclarar | null }).obligacion_declarar ?? null) : null;
  const hasIncome = FIELDS.some((f) => (edits[f.name] ?? baseline[f.name] ?? "") !== "" && f.name.startsWith("rendimiento"));

  function lever(benefit: Benefit) {
    const preset = LEVER_PRESET[benefit.id];
    return (
      <li key={benefit.id} className="rounded-xl border border-line bg-surface px-3.5 py-3">
        <div className="flex items-start justify-between gap-3">
          <p className="text-[14px] font-semibold text-ink">{benefit.nombre}</p>
          {benefit.ahorro_estimado && Number(benefit.ahorro_estimado) > 0 && (
            <span className="flex-shrink-0 rounded-pill bg-grounding-cited-bg px-2 py-0.5 text-[12px] font-bold text-grounding-cited-fg">
              hasta −{formatMoney(benefit.ahorro_estimado)}
            </span>
          )}
        </div>
        <p className="mt-1 text-[13px] leading-snug text-ink-soft">{benefit.detalle}</p>
        <p className="mt-1.5 text-[11.5px] text-muted">
          {benefit.referencia}
          {!benefit.verificado && " · cifras pendientes de contrastar"}
        </p>
        {preset && (
          <button
            type="button"
            onClick={() => acciones.setEdit(preset.field, preset.value)}
            className="mt-2 rounded-lg border border-line px-2.5 py-1 text-[12.5px] font-semibold text-primary-dark transition hover:border-primary"
          >
            Probar en la simulación
          </button>
        )}
      </li>
    );
  }

  function renderField(field: FieldDef) {
    const current = value(field.name);
    const dirty = field.name in cambios;
    if (field.kind === "flag") {
      return (
        <label key={field.name} className="flex items-start gap-2.5 sm:col-span-2">
          <input
            type="checkbox"
            checked={current === "true"}
            onChange={(e) => acciones.setEdit(field.name, String(e.target.checked))}
            className="mt-0.5 h-4 w-4 accent-[rgb(var(--primary))]"
          />
          <span>
            <span className={clsx("text-[13.5px]", dirty ? "font-semibold text-primary-dark" : "text-ink")}>{field.label}</span>
            {field.hint && <span className="block text-[11.5px] leading-snug text-muted">{field.hint}</span>}
          </span>
        </label>
      );
    }
    const fromChildren = field.name in derived;
    const sum = partsSum(field);
    const problem = totalProblem(field);
    const autoTotal = !!(field.parts || field.sumOf) && sum > 0 && !(parseAmount(current) ?? 0);
    const input = (
      <label className="block">
        <span className="text-[12.5px] text-ink-soft">{field.label}</span>
        <span className="relative mt-1 block">
          <input
            value={current}
            onChange={(e) => acciones.setEdit(field.name, e.target.value)}
            readOnly={fromChildren}
            aria-invalid={problem ? true : undefined}
            aria-describedby={problem ? `problem-${field.name}` : undefined}
            inputMode={field.kind === "money" ? "decimal" : "numeric"}
            maxLength={field.kind === "year" ? 4 : undefined}
            placeholder={autoTotal ? String(Math.round(sum * 100) / 100).replace(".", ",") : field.kind === "year" ? "AAAA" : "0"}
            className={clsx(
              INPUT_CLASS,
              field.kind === "money" && "pr-7",
              fromChildren && "cursor-default bg-line/40 text-ink-soft",
              problem ? "border-risk-high-fg" : dirty ? "border-primary" : "border-line focus:border-primary",
            )}
          />
          {field.kind === "money" && <span className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-[13px] text-muted">€</span>}
        </span>
        {fromChildren ? (
          <span className="mt-1 block text-[11.5px] leading-snug text-muted">Sale del detalle de cada hijo.</span>
        ) : problem ? (
          <span id={`problem-${field.name}`} role="alert" className="mt-1 block text-[11.5px] font-semibold leading-snug text-risk-high-fg">
            {problem}
          </span>
        ) : autoTotal ? (
          <span className="mt-1 block text-[11.5px] leading-snug text-muted">
            {field.sumOf ? `Vacío, se toma la suma de los indicados en «Familia»: ${sum}.` : `Sin total, se toma la suma del desglose: ${formatMoney(sum)}.`}
          </span>
        ) : (
          field.hint && <span className="mt-1 block text-[11.5px] leading-snug text-muted">{field.hint}</span>
        )}
      </label>
    );
    if (!field.parts) return <div key={field.name}>{input}</div>;
    const isOpen = openParts[field.name] ?? (sum > 0 || problem !== null);
    return (
      <div key={field.name} className="sm:col-span-2">
        {input}
        <details open={isOpen} className="group/parts mt-1.5">
          <summary
            // Opened and closed through state: a re-render (a new result arriving) must not undo the click.
            onClick={(e) => {
              e.preventDefault();
              acciones.setDesglose(field.name, !isOpen);
            }}
            className="flex cursor-pointer list-none items-center gap-1 text-[12.5px] font-semibold text-primary-dark [&::-webkit-details-marker]:hidden"
          >
            <ChevronDown size={14} aria-hidden className="transition group-open/parts:rotate-180" />
            Desglose por tipo{sum > 0 ? ` · ${formatMoney(sum)}` : " (opcional)"}
          </summary>
          <div className="mt-2 grid gap-3 border-l-2 border-line pl-3 sm:grid-cols-2">{field.parts.map(renderField)}</div>
          <p className="mt-1.5 pl-3 text-[11.5px] leading-snug text-muted">Algunas deducciones solo admiten una parte de estos gastos. Cada importe va en una sola casilla.</p>
        </details>
      </div>
    );
  }

  return (
    <div className="fixed inset-0 z-40 flex justify-end">
      <button type="button" aria-label="Cerrar el simulador" className="absolute inset-0 bg-ink/40" onClick={onClose} />
      <aside role="dialog" aria-modal="true" aria-label="Simulador de IRPF" className="relative flex h-full w-full max-w-[520px] flex-col border-l border-line bg-paper shadow-pop">
        <header className="flex items-center justify-between border-b border-line px-5 py-4">
          <div>
            <h2 className="flex items-center gap-2 font-serif text-[19px] font-bold text-ink">
              <Calculator size={18} aria-hidden className="text-primary-dark" />
              Simulador de IRPF{simulation ? ` ${simulation.ejercicio}` : ""}
            </h2>
            <p className="text-[12.5px] text-muted">Cambia una cifra y mira cómo cambia tu declaración</p>
          </div>
          <button type="button" onClick={onClose} aria-label="Cerrar" className="rounded-lg p-2 text-muted hover:bg-line/60 hover:text-ink">
            <X size={18} />
          </button>
        </header>

        <div className="flex-1 space-y-5 overflow-y-auto px-5 pb-5 pt-3">
          {error && !invalid && (
            <p role="alert" className="flex gap-2 rounded-lg border border-risk-high-fg/30 bg-risk-high-bg px-3 py-2 text-[13px] text-ink-soft">
              <TriangleAlert size={15} aria-hidden className="mt-0.5 flex-shrink-0 text-risk-high-fg" />
              <span>
                {error}
                {simulation && <span className="block text-[12px] text-muted">El resultado de abajo es el del último cálculo válido.</span>}
              </span>
            </p>
          )}

          {seed?.note && changed && (
            <p role="status" className="rounded-lg border border-primary/30 bg-primary-soft px-3 py-2 text-[13px] leading-snug text-primary-dark">
              {seed.note}
            </p>
          )}

          {sim && (
            <section aria-live="polite" className="sticky top-0 z-10 rounded-xl border border-line bg-surface px-4 py-4 shadow-pop">
              <p className="text-[12px] font-semibold uppercase tracking-[0.1em] text-muted">Resultado estimado</p>
              <p className={clsx("mt-1 font-serif text-[30px] font-bold leading-none", resultado > 0 ? "text-ink" : "text-grounding-cited-fg")}>
                {formatMoney(Math.abs(resultado))}
                <span className="ml-2 text-[14px] font-semibold text-ink-soft">{resultado > 0 ? "a pagar" : resultado < 0 ? "a devolver" : ""}</span>
              </p>
              <p className="mt-2 text-[13px] text-ink-soft">
                Cuota del año: {formatMoney(sim.resultado.cuota_liquida)} · tipo medio {sim.resultado.tipo_medio_efectivo.replace(".", ",")} %
              </p>
              {invalid ? (
                <p role="status" className="mt-2 flex gap-2 rounded-lg bg-risk-high-bg px-3 py-1.5 text-[13px] font-semibold text-risk-high-fg">
                  <TriangleAlert size={15} aria-hidden className="mt-0.5 flex-shrink-0" />
                  Hay datos que no cuadran (marcados en rojo más abajo). Este resultado no incluye tus últimos cambios.
                </p>
              ) : (
                changed && (
                  <p className={clsx("mt-2 rounded-lg px-3 py-1.5 text-[13.5px] font-semibold", ahorro > 0 ? "bg-grounding-cited-bg text-grounding-cited-fg" : ahorro < 0 ? "bg-grounding-uncited-bg text-grounding-uncited-fg" : "bg-grounding-abstain-bg text-grounding-abstain-fg")}>
                    {ahorro > 0 ? `Con estos cambios pagas ${formatMoney(ahorro)} menos` : ahorro < 0 ? `Con estos cambios pagas ${formatMoney(-ahorro)} más` : "Estos cambios no alteran el resultado"}
                    {base && <span className="font-normal"> (antes: {formatMoney(base.resultado.resultado)})</span>}
                  </p>
                )
              )}
              {!hasIncome && <p className="mt-2 text-[12.5px] text-muted">Introduce al menos tu salario bruto para obtener una estimación.</p>}
            </section>
          )}

          {obligacion && hasIncome && (
            <ObligacionDeclararCard
              obligacion={obligacion}
              value={(name) => edits[name] ?? baseline[name] ?? ""}
              onChange={acciones.setEdit}
              dirty={(name) => name in cambios}
            />
          )}

          {groups.map((group) => (
            <fieldset key={group.title}>
              <legend className="mb-2 text-[11px] font-semibold uppercase tracking-[0.12em] text-muted">{group.title}</legend>
              <div className="grid gap-3 sm:grid-cols-2">
                {group.fields.map(renderField)}
                {group.title === "Familia" && (
                  <EditorDescendientes rows={s.children} problems={s.childProblems} year={year} brokenSaved={s.savedChildren.broken && childrenEdit === null} onChange={acciones.setChildren} />
                )}
                {group.title === "Familia" && (
                  <label className="block sm:col-span-2">
                    <span className="text-[12.5px] text-ink-soft">Comunidad autónoma</span>
                    <select value={comunidad} onChange={(e) => acciones.setComunidad(e.target.value)} className="mt-1 w-full rounded-lg border border-line bg-surface px-3 py-2 text-[16px] text-ink focus:border-primary focus:outline-none sm:text-[14px]">
                      {COMUNIDADES.map((c) => (
                        <option key={c.value} value={c.value}>
                          {c.label}
                        </option>
                      ))}
                    </select>
                    <span className="mt-1 block text-[11.5px] leading-snug text-muted">
                      {comunidad === "Madrid"
                        ? "Se aplican la escala, los mínimos y varias deducciones de Madrid."
                        : comunidad === "Otra"
                          ? "Los territorios forales tienen su propio impuesto y Ceuta y Melilla una deducción específica: aquí la parte autonómica es solo una aproximación."
                          : comunidad
                            ? "Se aplican la escala, los mínimos y las deducciones más habituales de tu comunidad (abajo)."
                            : "Sin comunidad, la parte autonómica se aproxima con la escala estatal."}
                    </span>
                  </label>
                )}
                {group.title === "Familia" && (
                  <label className="block sm:col-span-2">
                    <span className="text-[12.5px] text-ink-soft">Tipo de declaración</span>
                    <select value={tributacion} onChange={(e) => acciones.setTributacion(e.target.value)} className="mt-1 w-full rounded-lg border border-line bg-surface px-3 py-2 text-[16px] text-ink focus:border-primary focus:outline-none sm:text-[14px]">
                      {TRIBUTACION.map((t) => (
                        <option key={t.value} value={t.value}>
                          {t.label}
                        </option>
                      ))}
                    </select>
                    {tributacion !== "individual" && (
                      <span className="mt-1 block text-[11.5px] leading-snug text-muted">
                        En conjunta, las cifras deben sumar las rentas de toda la unidad familiar.
                      </span>
                    )}
                  </label>
                )}
              </div>
            </fieldset>
          ))}

          {changed && (
            <div className="flex flex-wrap gap-2">
              {caseId && (
                <button type="button" onClick={() => void acciones.saveAll()} disabled={saving || busy || invalid} className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-3 py-2 text-[13px] font-semibold text-white transition hover:bg-primary-hover disabled:cursor-not-allowed disabled:opacity-50">
                  <Save size={14} aria-hidden />
                  {saving ? "Guardando…" : "Guardar en mi expediente"}
                </button>
              )}
              <button
                type="button"
                onClick={acciones.deshacer}
                className="inline-flex items-center gap-1.5 rounded-lg px-3 py-2 text-[13px] font-semibold text-ink-soft hover:bg-line/60"
              >
                <RotateCcw size={14} aria-hidden />
                Deshacer cambios
              </button>
              {saveError && (
                <p role="alert" className="w-full text-[12.5px] font-semibold text-risk-high-fg">
                  {saveError}
                </p>
              )}
            </div>
          )}

          {levers.length > 0 && (
            <section>
              <h3 className="mb-2 flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-[0.12em] text-muted">
                <Lightbulb size={13} aria-hidden />
                Formas de pagar menos
              </h3>
              <ul className="space-y-2">{levers.map(lever)}</ul>
            </section>
          )}

          {sim && (
            <details className="group rounded-xl border border-line bg-surface">
              <summary className="flex cursor-pointer list-none items-center justify-between px-4 py-2.5 text-[13px] font-semibold text-ink-soft [&::-webkit-details-marker]:hidden">
                Desglose del cálculo
                <ChevronDown size={16} aria-hidden className="text-muted transition group-open:rotate-180" />
              </summary>
              <table className="w-full border-t border-line text-[12.5px]">
                <tbody>
                  {sim.resultado.lineas.map((line, i) => (
                    <tr key={i} className="border-b border-line last:border-0">
                      <td className="px-4 py-1.5 text-ink-soft">
                        {line.concepto}
                        {line.referencia && <span className="block text-[11px] text-muted">{line.referencia}</span>}
                      </td>
                      <td className="whitespace-nowrap px-4 py-1.5 text-right font-mono tabular-nums text-ink">{formatMoney(line.importe)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </details>
          )}

          {sim && sim.resultado.avisos.length > 0 && (
            <details open className="group rounded-xl border border-grounding-uncited-bd bg-grounding-uncited-bg">
              <summary className="flex cursor-pointer list-none items-center justify-between px-4 py-2.5 text-[13px] font-semibold text-grounding-uncited-fg [&::-webkit-details-marker]:hidden">
                Qué no incluye esta estimación ({sim.resultado.avisos.length})
                <ChevronDown size={16} aria-hidden className="transition group-open:rotate-180" />
              </summary>
              <ul className="list-disc space-y-1.5 px-4 pb-3 pl-8 text-[12.5px] leading-snug text-ink-soft">
                {sim.resultado.avisos.map((aviso, i) => (
                  <li key={i}>{aviso}</li>
                ))}
              </ul>
            </details>
          )}
        </div>

        <footer className="border-t border-line px-5 py-3 text-[11.5px] leading-relaxed text-muted">
          Cálculo determinista con los parámetros estatales del ejercicio. No sustituye al borrador de la Agencia Tributaria.
          {sim && <span className="block">{sim.resultado.fuente}.</span>}
        </footer>
      </aside>
    </div>
  );
}
