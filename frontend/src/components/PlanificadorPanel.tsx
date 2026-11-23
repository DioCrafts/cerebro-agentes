/* ui-legado: pendiente de migrar a src/ui (docs/ui/sistema-de-diseno.md) */
// «Planificar el hogar»: what to do with a sum of money — repay the mortgage early (term or
// instalment), contribute to pension plans, donate within a budget or keep it liquid —, from the
// deterministic planner behind POST /planificar. No model call: every figure is the plan's; the
// screen never adds up figures of different modules by itself.
//
// Two tabs: «Tu dinero» (amount, horizon, cushion, the alternative return the user assumes and the
// levers they rule out) and «Tu plan» (allocation, net position in both views of the pension, each
// lever inside the plan and alone, scenarios, aids, assumptions and what is not modelled). Nothing is
// stored: the inputs live in this screen only.

import { Check, ChevronDown, CircleAlert, Info, Landmark, Loader2, Scale, TriangleAlert, X } from "lucide-react";
import clsx from "clsx";
import { type FormEvent, type ReactNode, useEffect, useMemo, useRef, useState } from "react";
import { flushSync } from "react-dom";

import { ApiError } from "../api";
import type { PalancaId, PlanHogar } from "../apiPlanificador";
import { planificar } from "../apiPlanificador";
import { boeUrl, eur, splitQuotes } from "../lib/optimizador/textos";
import {
  CAUSA_NO_USADA,
  ESTADO_AYUDA,
  FORMULARIO_INICIAL,
  type FormularioPlan,
  HORIZONTES,
  NATURALEZA_COMPONENTE,
  PALANCAS,
  humanizePlanError,
  peticion,
  problemasDe,
} from "../lib/planificador";
import { formatDate } from "../lib/presentation";

type Tab = "datos" | "plan";

interface PlanificadorPanelProps {
  open: boolean;
  onClose: () => void;
  caseId?: string | null;
  taxPeriod?: string | null;
}

function Campo({
  id,
  label,
  hint,
  error,
  children,
}: {
  id: string;
  label: string;
  hint?: string;
  error?: string;
  children: ReactNode;
}) {
  return (
    <div className="flex flex-col gap-1">
      <label htmlFor={id} className="text-[13px] font-semibold text-ink">
        {label}
      </label>
      {children}
      {hint && !error && (
        <p id={`${id}-hint`} className="text-[12px] leading-snug text-muted">
          {hint}
        </p>
      )}
      {error && (
        <p id={`${id}-error`} role="alert" className="text-[12px] font-semibold text-risk-high-fg">
          {error}
        </p>
      )}
    </div>
  );
}

const INPUT = "w-full rounded-lg border border-line bg-surface px-3 py-2 text-[14px] text-ink outline-hidden focus:border-primary";

function Cita({ texto }: { texto: string }) {
  return (
    <>
      {splitQuotes(texto).map((part, i) =>
        part.quote ? (
          <q key={i} className="italic text-ink-soft">
            {part.text}
          </q>
        ) : (
          <span key={i}>{part.text}</span>
        ),
      )}
    </>
  );
}

function Seccion({ id, titulo, children, className }: { id: string; titulo: string; children: ReactNode; className?: string }) {
  return (
    <section aria-labelledby={id} className={clsx("rounded-xl border border-line bg-surface px-4 py-3.5", className)}>
      <h3 id={id} className="mb-2 font-serif text-[15.5px] font-bold text-ink">
        {titulo}
      </h3>
      {children}
    </section>
  );
}

export function PlanificadorPanel({ open, onClose, caseId, taxPeriod }: PlanificadorPanelProps) {
  const [tab, setTab] = useState<Tab>("datos");
  const [form, setForm] = useState<FormularioPlan>(FORMULARIO_INICIAL);
  const [enviado, setEnviado] = useState(false);
  const [computed, setComputed] = useState<{ plan: PlanHogar; key: string } | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const headlineRef = useRef<HTMLHeadingElement | null>(null);

  const ejercicio = taxPeriod && /^\d{4}$/.test(taxPeriod) ? Number(taxPeriod) : undefined;
  const problemas = useMemo(() => problemasDe(form), [form]);
  const body = useMemo(() => peticion(form, caseId, ejercicio), [form, caseId, ejercicio]);
  const key = JSON.stringify(body);
  const stale = computed !== null && computed.key !== key;

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);
  useEffect(() => () => abortRef.current?.abort(), []);

  if (!open) return null;

  const set = <K extends keyof FormularioPlan>(campo: K, valor: FormularioPlan[K]) => setForm((f) => ({ ...f, [campo]: valor }));
  const err = (campo: keyof FormularioPlan) => (enviado ? problemas[campo] : undefined);
  const aria = (id: string, campo: keyof FormularioPlan, hint = true) => ({
    id,
    "aria-invalid": err(campo) ? true : undefined,
    "aria-describedby": err(campo) ? `${id}-error` : hint ? `${id}-hint` : undefined,
  });

  async function calcular(event?: FormEvent) {
    event?.preventDefault();
    setEnviado(true);
    const primero = Object.keys(problemas)[0];
    if (primero) {
      document.getElementById(`plan-${primero}`)?.focus();
      return;
    }
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    setLoading(true);
    setError(null);
    try {
      const plan = await planificar(body, controller.signal);
      flushSync(() => {
        setComputed({ plan, key });
        setTab("plan");
      });
      headlineRef.current?.focus();
    } catch (e) {
      if (controller.signal.aborted) return;
      setError(e instanceof ApiError ? humanizePlanError(e.message, e.status) : "No se pudo calcular el plan: comprueba tu conexión e inténtalo de nuevo.");
      setTab("plan");
    } finally {
      if (abortRef.current === controller) {
        abortRef.current = null;
        setLoading(false);
      }
    }
  }

  function toggleExcluida(id: PalancaId) {
    setForm((f) => ({ ...f, excluidas: f.excluidas.includes(id) ? f.excluidas.filter((x) => x !== id) : [...f.excluidas, id] }));
  }

  function datosTab() {
    return (
      <form onSubmit={calcular} noValidate className="flex flex-col gap-4 pb-6">
        <p className="text-[13px] leading-snug text-ink-soft">
          Con tus cifras del expediente, la hipoteca y las ayudas (si tienes esos módulos activados), el planificador prueba
          todos los repartos y te dice cuál deja mejor a tu hogar al final del plazo que elijas.
        </p>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <Campo id="plan-importe" label="Dinero que puedes destinar ahora" hint="En euros" error={err("importe")}>
            <input {...aria("plan-importe", "importe")} inputMode="decimal" className={INPUT} value={form.importe} onChange={(e) => set("importe", e.target.value)} />
          </Campo>
          <Campo id="plan-excedente" label="Excedente mensual" hint="Lo que ahorras cada mes hasta diciembre (opcional)" error={err("excedente")}>
            <input {...aria("plan-excedente", "excedente")} inputMode="decimal" className={INPUT} value={form.excedente} onChange={(e) => set("excedente", e.target.value)} />
          </Campo>
          <Campo id="plan-horizonte" label="Horizonte" hint="Años hasta los que se compara">
            <select id="plan-horizonte" aria-describedby="plan-horizonte-hint" className={INPUT} value={form.horizonte} onChange={(e) => set("horizonte", Number(e.target.value))}>
              {HORIZONTES.map((h) => (
                <option key={h} value={h}>
                  {h} {h === 1 ? "año" : "años"}
                </option>
              ))}
            </select>
          </Campo>
          <Campo
            id="plan-rentabilidad"
            label="Rentabilidad alternativa (% anual neto)"
            hint="Lo que te daría el dinero que no uses. Es tu supuesto: no es una recomendación"
            error={err("rentabilidad")}
          >
            <input {...aria("plan-rentabilidad", "rentabilidad")} inputMode="decimal" className={INPUT} value={form.rentabilidad} placeholder="0" onChange={(e) => set("rentabilidad", e.target.value)} />
          </Campo>
        </div>

        <fieldset className="rounded-xl border border-line px-3 py-3">
          <legend className="px-1 text-[13px] font-semibold text-ink">Colchón de emergencia</legend>
          <div className="mb-2 flex flex-wrap gap-3 text-[13px]">
            {(
              [
                ["meses", "Meses de gastos"],
                ["importe", "Una cantidad"],
                ["ninguno", "Sin colchón"],
              ] as Array<[FormularioPlan["colchonModo"], string]>
            ).map(([modo, label]) => (
              <label key={modo} className="flex items-center gap-1.5">
                <input type="radio" name="plan-colchon-modo" checked={form.colchonModo === modo} onChange={() => set("colchonModo", modo)} />
                {label}
              </label>
            ))}
          </div>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            {form.colchonModo === "meses" && (
              <>
                <Campo id="plan-gastos" label="Gastos del hogar al mes" error={err("gastos")}>
                  <input {...aria("plan-gastos", "gastos", false)} inputMode="decimal" className={INPUT} value={form.gastos} onChange={(e) => set("gastos", e.target.value)} />
                </Campo>
                <Campo id="plan-meses" label="Meses de colchón" error={err("meses")}>
                  <input {...aria("plan-meses", "meses", false)} inputMode="numeric" className={INPUT} value={form.meses} onChange={(e) => set("meses", e.target.value)} />
                </Campo>
              </>
            )}
            {form.colchonModo === "importe" && (
              <Campo id="plan-colchon" label="Colchón que quieres mantener" error={err("colchon")}>
                <input {...aria("plan-colchon", "colchon", false)} inputMode="decimal" className={INPUT} value={form.colchon} onChange={(e) => set("colchon", e.target.value)} />
              </Campo>
            )}
            {form.colchonModo !== "ninguno" && (
              <Campo id="plan-liquidez" label="Ahorros líquidos que ya tienes aparte" hint="Cuentan para el colchón" error={err("liquidez")}>
                <input {...aria("plan-liquidez", "liquidez")} inputMode="decimal" className={INPUT} value={form.liquidez} onChange={(e) => set("liquidez", e.target.value)} />
              </Campo>
            )}
          </div>
        </fieldset>

        <fieldset className="rounded-xl border border-line px-3 py-3">
          <legend className="px-1 text-[13px] font-semibold text-ink">Palancas</legend>
          <p className="mb-2 text-[12px] text-muted">Desmarca las que no quieras que se propongan.</p>
          <div className="grid grid-cols-1 gap-1.5 sm:grid-cols-2">
            {PALANCAS.map((p) => (
              <label key={p.id} className="flex items-center gap-2 text-[13.5px] text-ink">
                <input type="checkbox" checked={!form.excluidas.includes(p.id)} onChange={() => toggleExcluida(p.id)} />
                {p.label}
              </label>
            ))}
          </div>
          <label className="mt-2 flex items-start gap-2 text-[13px] text-ink">
            <input type="checkbox" className="mt-0.5" checked={form.protegerAyudas} onChange={(e) => set("protegerAyudas", e.target.checked)} />
            <span>No proponer nada que me haga perder o rebajar una ayuda pública que hoy me corresponde</span>
          </label>
        </fieldset>

        <details className="rounded-xl border border-line px-3 py-2">
          <summary className="flex cursor-pointer items-center gap-1 text-[13px] font-semibold text-ink">
            <ChevronDown size={14} aria-hidden /> Pensiones, donativo e hipoteca compartida
          </summary>
          <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2">
            <Campo id="plan-rescate" label="Tipo al rescatar el plan (%)" hint="Tu tipo marginal esperado en la jubilación" error={err("rescate")}>
              <input {...aria("plan-rescate", "rescate")} inputMode="decimal" className={INPUT} value={form.rescate} onChange={(e) => set("rescate", e.target.value)} />
            </Campo>
            <Campo id="plan-vista" label="Valorar el plan de pensiones como" hint="Las dos vistas se muestran siempre">
              <select id="plan-vista" aria-describedby="plan-vista-hint" className={INPUT} value={form.vista} onChange={(e) => set("vista", e.target.value as FormularioPlan["vista"])}>
                <option value="auto">Automático</option>
                <option value="diferimiento">Diferimiento (ahorro de hoy)</option>
                <option value="definitivo">Ahorro definitivo (descontando el rescate)</option>
              </select>
            </Campo>
            <Campo id="plan-donativo" label="Donativo que quieres hacer" error={err("donativo")}>
              <input {...aria("plan-donativo", "donativo", false)} inputMode="decimal" className={INPUT} value={form.donativo} onChange={(e) => set("donativo", e.target.value)} />
            </Campo>
            <Campo id="plan-presupuesto" label="Máximo que donarías" error={err("presupuesto")}>
              <input {...aria("plan-presupuesto", "presupuesto", false)} inputMode="decimal" className={INPUT} value={form.presupuesto} onChange={(e) => set("presupuesto", e.target.value)} />
            </Campo>
            <Campo id="plan-parteConyuge" label="Parte de la hipoteca que paga tu cónyuge (%)" hint="Para la deducción por vivienda anterior a 2013" error={err("parteConyuge")}>
              <input {...aria("plan-parteConyuge", "parteConyuge")} inputMode="decimal" className={INPUT} value={form.parteConyuge} onChange={(e) => set("parteConyuge", e.target.value)} />
            </Campo>
          </div>
        </details>

        <button
          type="submit"
          disabled={loading}
          className="flex items-center justify-center gap-2 rounded-lg bg-primary px-4 py-2.5 text-[14px] font-semibold text-white transition hover:bg-primary-hover disabled:opacity-60"
        >
          {loading ? <Loader2 size={16} aria-hidden className="animate-spin" /> : <Scale size={16} aria-hidden />}
          {loading ? "Calculando…" : "Calcular el mejor reparto"}
        </button>
      </form>
    );
  }

  function planTab() {
    if (!computed) {
      return (
        <div className="rounded-xl border border-dashed border-line px-4 py-6 text-center text-[13px] text-muted">
          {error ? null : "Rellena «Tu dinero» y pulsa «Calcular el mejor reparto»."}
        </div>
      );
    }
    const { plan } = computed;
    const v = plan.valoracion;
    const otraVista = plan.vista === "diferimiento" ? "ahorro definitivo" : "diferimiento";
    const otraTotal = plan.vista === "diferimiento" ? v.total_definitivo : v.total_diferimiento;
    return (
      <div className="flex flex-col gap-4">
        {stale && (
          <p className="flex gap-2 rounded-lg border border-grounding-uncited-bd bg-grounding-uncited-bg px-3 py-2 text-[13px] text-ink-soft">
            <Info size={15} aria-hidden className="mt-0.5 shrink-0" />
            Has cambiado los datos: este plan es del cálculo anterior. Vuelve a calcular.
          </p>
        )}
        <section aria-labelledby="plan-titular" className="rounded-xl border-2 border-primary/40 bg-primary-soft px-4 py-3.5">
          <h3 id="plan-titular" ref={headlineRef} tabIndex={-1} className="font-serif text-[17px] font-bold text-ink outline-hidden">
            Posición neta en {plan.horizonte_anos} {plan.horizonte_anos === 1 ? "año" : "años"}: {eur(v.total)}
          </h3>
          <p className="mt-1 text-[13px] leading-snug text-ink-soft">
            Frente a dejar {eur(plan.importe_total)} líquidos al {plan.rentabilidad_alternativa_pct.replace(".", ",")} % anual, hasta el{" "}
            {formatDate(plan.fecha_horizonte)}. {plan.vista === "diferimiento" ? "Contando el plan de pensiones como diferimiento" : "Descontando el impuesto al rescatar el plan"}; en la vista de {otraVista}: {eur(otraTotal)}. En euros de hoy: {eur(v.valor_actual)}.
          </p>
        </section>

        <Seccion id="plan-reparto" titulo="Reparto">
          <table className="w-full text-[13.5px]">
            <caption className="sr-only">Reparto del dinero</caption>
            <tbody>
              {plan.asignacion.map((a) => (
                <tr key={a.id} className="border-b border-line/70 last:border-0">
                  <th scope="row" className="py-1.5 pr-2 text-left font-semibold text-ink">
                    {a.nombre}
                    <span className="block text-[12px] font-normal leading-snug text-muted">{a.detalle}</span>
                  </th>
                  <td className="whitespace-nowrap py-1.5 text-right tabular-nums text-ink">{eur(a.importe)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Seccion>

        {plan.palancas.length > 0 && (
          <section aria-labelledby="plan-palancas">
            <h3 id="plan-palancas" className="mb-2 font-serif text-[15.5px] font-bold text-ink">
              Cada palanca, dentro del plan y sola
            </h3>
            <ul className="flex flex-col gap-2.5">
              {plan.palancas.map((p) => (
                <li key={p.id} className="rounded-xl border border-line bg-surface px-4 py-3.5 shadow-card">
                  <p className="flex items-baseline justify-between gap-2">
                    <span className="font-semibold text-ink">{p.nombre}</span>
                    <span className="tabular-nums text-ink">{eur(p.importe)}</span>
                  </p>
                  <p className="mt-0.5 text-[12.5px] text-muted">{p.naturaleza}</p>
                  <dl className="mt-2 grid grid-cols-2 gap-2 text-[13px]">
                    <div>
                      <dt className="text-muted">Dentro del plan</dt>
                      <dd className="font-semibold tabular-nums text-ink">{eur(p.valor_marginal)}</dd>
                    </div>
                    <div>
                      <dt className="text-muted">Sola (con {eur(p.importe_aislado)})</dt>
                      <dd className="font-semibold tabular-nums text-ink">{eur(p.valor_aislado)}</dd>
                    </div>
                  </dl>
                  <p className="mt-2 text-[12.5px] leading-snug text-ink-soft">{p.explicacion}</p>
                  {p.referencias.length > 0 && <p className="mt-1 text-[11.5px] text-muted">{p.referencias.join(" · ")}</p>}
                </li>
              ))}
            </ul>
            <p className="mt-2 text-[12.5px] text-ink-soft">
              Por separado suman {eur(plan.suma_aislados)}; el plan, {eur(v.total)} (diferencia {eur(plan.interaccion)}).
            </p>
          </section>
        )}

        {plan.explicacion_interaccion.length > 0 && (
          <Seccion id="plan-interaccion" titulo="Por qué la combinación no es la suma">
            <ul className="list-disc space-y-1 pl-5 text-[13px] leading-snug text-ink-soft">
              {plan.explicacion_interaccion.map((x) => (
                <li key={x}>{x}</li>
              ))}
            </ul>
          </Seccion>
        )}

        <Seccion id="plan-cuentas" titulo="Las cuentas, línea a línea">
          <ul className="flex flex-col gap-1.5 text-[13px]">
            {v.componentes.map((c) => (
              <li key={c.concepto} className="flex items-start justify-between gap-3">
                <span className="text-ink-soft">
                  {c.concepto}{" "}
                  <span className={clsx("ml-1 inline-block rounded-pill border px-1.5 text-[11px] font-semibold", NATURALEZA_COMPONENTE[c.naturaleza].cls)}>
                    {NATURALEZA_COMPONENTE[c.naturaleza].label}
                  </span>
                  <span className="block text-[11.5px] text-muted">
                    Módulo {c.modulo}
                    {c.referencia ? ` · ${c.referencia}` : ""}
                  </span>
                </span>
                <span className="whitespace-nowrap tabular-nums text-ink">{eur(c.importe)}</span>
              </li>
            ))}
          </ul>
          <p className="mt-2 border-t border-line pt-2 text-[13px] text-ink">
            IRPF de {plan.ejercicio} con el plan: {eur(v.impuesto_ejercicio)} ({v.tributacion.replace("_", " ")}).
          </p>
        </Seccion>

        <Seccion id="plan-escenarios" titulo="Comparado con otras opciones">
          <ul className="flex flex-col gap-1 text-[13px]">
            {plan.escenarios.map((e) => (
              <li key={e.nombre} className="flex justify-between gap-3">
                <span className={clsx(e.nombre === "Plan óptimo" ? "font-semibold text-ink" : "text-ink-soft")}>
                  {e.nombre}
                  {!e.factible && <span className="ml-1 text-[11.5px] text-risk-high-fg">(no respeta tus límites)</span>}
                </span>
                <span className="whitespace-nowrap tabular-nums text-ink">{eur(e.total)}</span>
              </li>
            ))}
          </ul>
        </Seccion>

        {plan.hipoteca && Number(plan.hipoteca.importe) > 0 && (
          <Seccion id="plan-hipoteca" titulo="Tu hipoteca con el plan">
            <dl className="grid grid-cols-2 gap-2 text-[13px]">
              <div>
                <dt className="text-muted">Cuota</dt>
                <dd className="tabular-nums text-ink">
                  {eur(plan.hipoteca.cuota_actual)} → {plan.hipoteca.cuota_nueva !== null ? eur(plan.hipoteca.cuota_nueva) : "—"}
                </dd>
              </div>
              <div>
                <dt className="text-muted">Última cuota</dt>
                <dd className="tabular-nums text-ink">
                  {formatDate(plan.hipoteca.fin_actual)} → {plan.hipoteca.fin_nuevo ? formatDate(plan.hipoteca.fin_nuevo) : "—"}
                </dd>
              </div>
              <div>
                <dt className="text-muted">Comisión {plan.hipoteca.comision_es_maximo ? "(máximo legal)" : ""}</dt>
                <dd className="tabular-nums text-ink">{eur(plan.hipoteca.comision)}</dd>
              </div>
              <div>
                <dt className="text-muted">Intereses ahorrados (todo el préstamo)</dt>
                <dd className="tabular-nums text-ink">{eur(plan.hipoteca.intereses_ahorrados_total)}</dd>
              </div>
            </dl>
            {plan.hipoteca.fundamento_comision.length > 0 && (
              <ul className="mt-2 list-disc space-y-0.5 pl-5 text-[12px] text-muted">
                {plan.hipoteca.fundamento_comision.map((x) => (
                  <li key={x}>{x}</li>
                ))}
              </ul>
            )}
          </Seccion>
        )}

        {plan.ayudas.length > 0 && (
          <Seccion id="plan-ayudas" titulo="Ayudas públicas">
            <ul className="flex flex-col gap-2.5">
              {plan.ayudas.map((a) => {
                const cambia = a.estado_antes !== a.estado_despues || a.importe_antes !== a.importe_despues;
                return (
                  <li key={a.id} className={clsx("rounded-lg border px-3 py-2 text-[13px]", cambia ? "border-risk-high-fg/40 bg-risk-high-bg" : "border-line")}>
                    <p className="flex items-center justify-between gap-2 font-semibold text-ink">
                      <span>{a.nombre}</span>
                      <span className="text-[12px] font-normal text-ink-soft">
                        {ESTADO_AYUDA[a.estado_antes] ?? a.estado_antes}
                        {cambia ? ` → ${ESTADO_AYUDA[a.estado_despues] ?? a.estado_despues}` : ""}
                      </span>
                    </p>
                    <p className="mt-0.5 text-[12.5px] text-ink-soft">Mide: {a.magnitud}.</p>
                    <p className="mt-0.5 text-[12.5px] leading-snug text-ink-soft">{a.explicacion}</p>
                    <details className="mt-1">
                      <summary className="cursor-pointer text-[12px] text-primary-dark">La norma</summary>
                      <p className="mt-1 text-[12px] leading-snug text-muted">
                        <Cita texto={a.cita} />
                      </p>
                    </details>
                  </li>
                );
              })}
            </ul>
          </Seccion>
        )}

        {plan.no_usadas.length > 0 && (
          <Seccion id="plan-no-usadas" titulo="Lo que no entra en el plan">
            <ul className="flex flex-col gap-1.5 text-[13px]">
              {plan.no_usadas.map((n) => (
                <li key={`${n.id}-${n.causa}`}>
                  <span className="font-semibold text-ink">{n.nombre}</span>{" "}
                  <span className="rounded-pill border border-line px-1.5 text-[11px] text-muted">{CAUSA_NO_USADA[n.causa]}</span>
                  <span className="block text-[12.5px] leading-snug text-ink-soft">{n.detalle}</span>
                </li>
              ))}
            </ul>
          </Seccion>
        )}

        {(plan.supuestos.length > 0 || plan.avisos.length > 0) && (
          <Seccion id="plan-supuestos" titulo="Supuestos y avisos">
            <ul className="list-disc space-y-1 pl-5 text-[12.5px] leading-snug text-ink-soft">
              {plan.supuestos.map((s) => (
                <li key={s}>{s}</li>
              ))}
              {plan.avisos.map((s) => (
                <li key={s} className="text-ink">
                  <CircleAlert size={12} aria-hidden className="mr-1 inline text-risk-high-fg" />
                  {s}
                </li>
              ))}
            </ul>
          </Seccion>
        )}

        <details className="rounded-xl border border-line bg-surface px-4 py-3">
          <summary className="cursor-pointer text-[13.5px] font-semibold text-ink">Lo que no se ha modelado y cómo se ha buscado</summary>
          <ul className="mt-2 list-disc space-y-1 pl-5 text-[12.5px] leading-snug text-ink-soft">
            {plan.no_modelado.map((s) => (
              <li key={s}>{s}</li>
            ))}
          </ul>
          <p className="mt-2 text-[12px] leading-snug text-muted">
            {plan.busqueda.combinaciones.toLocaleString("es-ES")} combinaciones, paso {eur(plan.busqueda.resolucion)}
            {plan.busqueda.resolucion_hipoteca ? ` (hipoteca: ${eur(plan.busqueda.resolucion_hipoteca)})` : ""}. {plan.busqueda.nota}
          </p>
        </details>

        {plan.textos_legales.length > 0 && (
          <details className="rounded-xl border border-line bg-surface px-4 py-3">
            <summary className="flex cursor-pointer items-center gap-1.5 text-[13.5px] font-semibold text-ink">
              <Landmark size={14} aria-hidden /> Textos legales
            </summary>
            <ul className="mt-2 flex flex-col gap-2 text-[12.5px] leading-snug text-ink-soft">
              {plan.textos_legales.map((t) => {
                const url = boeUrl(t.boe_id);
                return (
                  <li key={t.referencia}>
                    <span className="font-semibold text-ink">{t.referencia}</span>{" "}
                    {url ? (
                      <a href={url} target="_blank" rel="noreferrer" className="text-primary-dark underline">
                        {t.boe_id}
                      </a>
                    ) : (
                      t.boe_id
                    )}
                    <q className="mt-0.5 block italic">{t.texto}</q>
                  </li>
                );
              })}
            </ul>
          </details>
        )}
        <p className="text-[12px] text-muted">No es una recomendación de inversión: son cuentas con tus supuestos.</p>
      </div>
    );
  }

  return (
    <div className="fixed inset-0 z-40 flex justify-end">
      <button type="button" aria-label="Cerrar el planificador" className="absolute inset-0 bg-ink/40" onClick={onClose} />
      <aside role="dialog" aria-modal="true" aria-labelledby="plan-title" className="relative flex h-full w-full max-w-[560px] flex-col border-l border-line bg-paper shadow-pop">
        <header className="border-b border-line px-4 pt-4 sm:px-5">
          <div className="flex items-start justify-between gap-2">
            <div className="min-w-0">
              <h2 id="plan-title" className="flex items-center gap-2 font-serif text-[19px] font-bold text-ink">
                <Scale size={18} aria-hidden className="shrink-0 text-primary-dark" />
                Planificar el hogar
              </h2>
              <p className="text-[12.5px] leading-snug text-muted">¿Amortizar, aportar al plan, donar o guardar? El mejor reparto de tu dinero</p>
            </div>
            <button type="button" onClick={onClose} aria-label="Cerrar" className="rounded-lg p-2 text-muted hover:bg-line/60 hover:text-ink">
              <X size={18} />
            </button>
          </div>
          <div role="tablist" aria-label="Planificador" className="mt-3 flex gap-1">
            {(
              [
                ["datos", "1. Tu dinero"],
                ["plan", "2. Tu plan"],
              ] as Array<[Tab, string]>
            ).map(([id, label]) => (
              <button
                key={id}
                type="button"
                role="tab"
                id={`plan-tab-${id}`}
                aria-selected={tab === id}
                aria-controls="plan-tabpanel"
                tabIndex={tab === id ? 0 : -1}
                onClick={() => setTab(id)}
                onKeyDown={(e) => {
                  if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
                    const next: Tab = id === "datos" ? "plan" : "datos";
                    setTab(next);
                    document.getElementById(`plan-tab-${next}`)?.focus();
                  }
                }}
                className={clsx(
                  "-mb-px inline-flex items-center gap-1.5 whitespace-nowrap border-b-2 px-3 py-2 text-[13px] font-semibold transition",
                  tab === id ? "border-primary text-primary-dark" : "border-transparent text-ink-soft hover:text-ink",
                )}
              >
                {label}
                {id === "plan" && computed && !stale && <Check size={13} aria-hidden className="text-grounding-cited-fg" />}
                {id === "plan" && stale && <span className="rounded-pill bg-grounding-uncited-bg px-1.5 text-[11px] font-semibold text-grounding-uncited-fg">desactualizado</span>}
              </button>
            ))}
          </div>
        </header>
        <div role="tabpanel" id="plan-tabpanel" aria-labelledby={`plan-tab-${tab}`} className="flex-1 overflow-y-auto overflow-x-hidden px-4 pt-4 pb-6 sm:px-5">
          {error && tab === "plan" && (
            <p role="alert" className="mb-4 flex gap-2 rounded-lg border border-risk-high-fg/30 bg-risk-high-bg px-3 py-2 text-[13px] text-ink-soft">
              <TriangleAlert size={15} aria-hidden className="mt-0.5 shrink-0 text-risk-high-fg" />
              {error}
            </p>
          )}
          {loading && (
            <p role="status" className="sr-only">
              Calculando el mejor reparto…
            </p>
          )}
          {tab === "datos" ? datosTab() : planTab()}
        </div>
        <footer className="border-t border-line px-4 py-2.5 text-[11.5px] leading-snug text-muted sm:px-5">
          Cálculo determinista, sin inteligencia artificial. Nada de esta pantalla se guarda.
        </footer>
      </aside>
    </div>
  );
}
