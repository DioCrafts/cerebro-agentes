// «Tu plan»: the best combination of the choices the law leaves to the household, as the
// optimiser computed it. Every figure is the plan's; standalone savings are never added up.

import { ArrowRight, BookOpen, ExternalLink, Info, Loader2, RotateCcw, TriangleAlert } from "lucide-react";
import clsx from "clsx";
import type { RefObject } from "react";

import type { PalancaNoDisponible } from "../../apiOptimizador";
import { CAUSA_LABEL, boeUrl, declaracionTitulo, enMinuscula, eur, joinList, missingLabel, planEnSimulador, resultadoTexto, tributacionTexto } from "../../lib/optimizador/textos";
import { FIELD_BY_NAME } from "../../lib/optimizador/campos";
import { formatDate } from "../../lib/presentation";
import { PalancaCard } from "./PalancaCard";
import { Badge, Collapsible, Quoted, Trace } from "./piezas";
import type { Optimizador } from "./useOptimizador";

interface Props {
  o: Optimizador;
  caseId: string | null;
  puedeUsarEnSimulador: boolean;
  headlineRef: RefObject<HTMLElement | null>;
}

function BotonesCompletar({ names, onCompletar }: { names: string[]; onCompletar: (name: string) => void }) {
  return (
    <div className="mt-2 flex flex-wrap gap-2">
      {names.map((name) => {
        const inForm = name === "conyuge" || name === "estado_civil" || name === "comunidad_autonoma" || name === "perdidas_latentes" || FIELD_BY_NAME.has(name);
        return (
          <button
            key={name}
            type="button"
            onClick={() => onCompletar(name)}
            className="inline-flex items-center gap-1 rounded-lg border border-primary/40 bg-surface px-2.5 py-1 text-[12.5px] font-semibold text-primary-dark transition hover:border-primary"
          >
            {inForm ? `Completar: ${enMinuscula(missingLabel(name))}` : `${missingLabel(name)}: abrir el simulador`}
            <ArrowRight size={13} aria-hidden />
          </button>
        );
      })}
    </div>
  );
}

function NoDisponible({ nd, onCompletar }: { nd: PalancaNoDisponible; onCompletar: (name: string) => void }) {
  return (
    <li className="rounded-xl border border-line bg-surface px-3.5 py-3">
      <div className="flex flex-wrap items-center gap-2">
        <p className="text-[13.5px] font-semibold text-ink">{nd.nombre}</p>
        <Badge className={nd.causa === "faltan_datos" ? "border-primary/40 bg-primary-soft text-primary-dark" : "border-grounding-abstain-bd bg-grounding-abstain-bg text-grounding-abstain-fg"}>{CAUSA_LABEL[nd.causa]}</Badge>
      </div>
      <p className="mt-1 text-[12.5px] leading-snug text-ink-soft">
        <Quoted text={nd.detalle} />
      </p>
      {nd.datos_que_faltan.length > 0 && <BotonesCompletar names={nd.datos_que_faltan} onCompletar={onCompletar} />}
    </li>
  );
}

export function OptimizadorPlan({ o, caseId, puedeUsarEnSimulador, headlineRef }: Props) {
  const { state, stale, acciones } = o;
  const { computed, loading } = state;
  const { goToData, completar, sendToSimulator, section: sec, toggleSection: toggle } = acciones;
  if (!computed) {
    return (
      <div className="rounded-xl border border-dashed border-line px-4 py-6 text-center">
        <p className="text-[13.5px] text-ink-soft">{loading ? "Calculando tu plan…" : "Aún no has calculado tu plan."}</p>
        {!loading && (
          <button type="button" onClick={goToData} className="mt-2 inline-flex items-center gap-1 text-[13px] font-semibold text-primary-dark hover:underline">
            Revisa tus datos y pulsa «Calcular mi plan»
            <ArrowRight size={14} aria-hidden />
          </button>
        )}
      </div>
    );
  }
  const { plan } = computed;
  const ahorro = Number(plan.ahorro_total);
  const interaccion = Number(plan.interaccion);
  const diferidas = plan.palancas.filter((p) => p.naturaleza === "diferimiento");
  const gastos = plan.palancas.filter((p) => p.naturaleza === "requiere_gastar");
  const simulador = planEnSimulador(plan, () => 0);
  return (
    <div className="space-y-5">
      {stale && (
        <div role="status" className="sticky top-0 z-10 rounded-xl border border-grounding-uncited-bd bg-grounding-uncited-bg px-4 py-3 shadow-pop">
          <p className="flex gap-2 text-[13px] font-semibold text-grounding-uncited-fg">
            <TriangleAlert size={16} aria-hidden className="mt-0.5 flex-shrink-0" />
            Plan desactualizado: has cambiado datos desde que se calculó.
          </p>
          <div className="mt-2 flex flex-wrap gap-2">
            <button
              type="button"
              onClick={() => void acciones.calcular()}
              disabled={loading}
              className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-3 py-1.5 text-[13px] font-semibold text-white transition hover:bg-primary-hover disabled:opacity-70"
            >
              {loading ? <Loader2 size={14} aria-hidden className="animate-spin" /> : <RotateCcw size={14} aria-hidden />}
              {loading ? "Calculando…" : "Recalcular"}
            </button>
            <button type="button" onClick={goToData} className="rounded-lg px-3 py-1.5 text-[13px] font-semibold text-ink-soft hover:bg-line/60">
              Ver mis datos
            </button>
          </div>
        </div>
      )}

      <div className={clsx("space-y-5 transition-opacity", (stale || loading) && "opacity-50")} aria-busy={loading || undefined}>
        <section
          ref={headlineRef}
          tabIndex={-1}
          aria-labelledby="opt-headline"
          className="scroll-mt-4 rounded-xl border border-line bg-surface px-4 py-4 shadow-pop focus:outline-none focus-visible:ring-2 focus-visible:ring-primary"
        >
          <p className="text-[12px] font-semibold uppercase tracking-[0.1em] text-muted">
            Tu plan para el IRPF {plan.ejercicio}
            {stale && <span className="ml-1 normal-case tracking-normal text-grounding-uncited-fg">· desactualizado</span>}
          </p>
          <h3 id="opt-headline" className={clsx("mt-1 font-serif font-bold leading-tight", ahorro > 0 ? "text-[28px] text-grounding-cited-fg" : "text-[21px] text-ink")}>
            {ahorro > 0 ? `Puedes ahorrar ${eur(plan.ahorro_total)}` : "Con estos datos no hay nada que mejorar"}
          </h3>
          <p className="mt-1 text-[13px] text-ink-soft">
            {ahorro > 0
              ? `en tu IRPF de ${plan.ejercicio}, frente a lo que pagarías ahora`
              : plan.fase === "solo_opciones_de_declaracion"
                ? "El ejercicio ya ha terminado y ninguna opción de la declaración lo reduce."
                : "Ninguna combinación de las opciones disponibles reduce tu impuesto."}
          </p>
          <dl className="mt-3 grid gap-2 text-[13px] sm:grid-cols-2">
            <div className="rounded-lg bg-paper px-3 py-2">
              <dt className="text-[11.5px] text-muted">Ahora</dt>
              <dd className="font-semibold text-ink">{resultadoTexto(plan.base.resultado_total)}</dd>
              <dd className="block text-[12px] leading-snug text-ink-soft">{tributacionTexto(plan.base)}</dd>
            </div>
            <div className="rounded-lg bg-paper px-3 py-2">
              <dt className="text-[11.5px] text-muted">Con el plan</dt>
              <dd className="font-semibold text-ink">{resultadoTexto(plan.optimo.resultado_total)}</dd>
              <dd className="block text-[12px] leading-snug text-ink-soft">{tributacionTexto(plan.optimo)}</dd>
            </div>
          </dl>
          {(diferidas.length > 0 || gastos.length > 0) && (
            <p className="mt-3 flex gap-2 rounded-lg border border-grounding-uncited-bd bg-grounding-uncited-bg px-3 py-2 text-[12.5px] leading-snug text-ink-soft">
              <Info size={15} aria-hidden className="mt-0.5 flex-shrink-0 text-grounding-uncited-fg" />
              <span>
                <span className="font-semibold text-ink">No todo es ahorro definitivo.</span>
                {diferidas.length > 0 && (
                  <>
                    {" "}
                    {joinList(diferidas.map((p) => `«${p.nombre}»`))} {diferidas.length === 1 ? "es un diferimiento" : "son diferimientos"}: pagas menos este año, pero ese impuesto
                    puede llegar más adelante.
                  </>
                )}
                {gastos.length > 0 && (
                  <>
                    {" "}
                    {joinList(gastos.map((p) => `«${p.nombre}»`))} exige gastar dinero
                    {gastos.some((p) => p.coste_neto !== null) &&
                      ` (coste neto: ${gastos
                        .filter((p) => p.coste_neto !== null)
                        .map((p) => eur(p.coste_neto ?? 0))
                        .join(" + ")})`}
                    .
                  </>
                )}
              </span>
            </p>
          )}
          {plan.fase === "solo_opciones_de_declaracion" && ahorro > 0 && (
            <p className="mt-2 text-[12.5px] leading-snug text-ink-soft">El ejercicio ya ha terminado: solo quedan las opciones de la declaración.</p>
          )}
          <p className="mt-3 text-[11.5px] leading-snug text-muted">
            {plan.busqueda.metodo === "exhaustiva"
              ? `Búsqueda exhaustiva: ${plan.busqueda.evaluaciones.toLocaleString("es-ES")} cálculos de la declaración, todas las combinaciones de candidatos. Plan para el ${formatDate(plan.fecha)}.`
              : `Plan para el ${formatDate(plan.fecha)}.`}
          </p>
        </section>

        {plan.busqueda.metodo === "heuristica" && (
          <p role="note" className="flex gap-2 rounded-xl border border-grounding-uncited-bd bg-grounding-uncited-bg px-4 py-3 text-[13px] leading-snug text-ink-soft">
            <TriangleAlert size={16} aria-hidden className="mt-0.5 flex-shrink-0 text-grounding-uncited-fg" />
            <span>
              <span className="font-semibold text-ink">Búsqueda aproximada ({plan.busqueda.evaluaciones.toLocaleString("es-ES")} cálculos).</span> {plan.busqueda.nota}
            </span>
          </p>
        )}

        {plan.palancas.length > 1 && (interaccion !== 0 || plan.explicacion_interaccion.length > 0) && (
          <section aria-labelledby="opt-interaccion" className="rounded-xl border-2 border-primary/40 bg-primary-soft px-4 py-3.5">
            <h3 id="opt-interaccion" className="text-[14px] font-bold text-primary-dark">
              {interaccion > 0
                ? `Juntas valen ${eur(interaccion)} más que por separado`
                : interaccion < 0
                  ? `Juntas valen ${eur(-interaccion)} menos que por separado`
                  : "Cómo se combinan las medidas"}
            </h3>
            <p className="mt-1 text-[12.5px] leading-snug text-ink">
              Las medidas se influyen entre sí, así que sus ahorros no se suman. Calculada cada una por separado, la suma daría {eur(plan.suma_ahorros_aislados)}; eso{" "}
              <strong>no</strong> es lo que ahorras. El ahorro real del plan es {eur(plan.ahorro_total)}.
            </p>
            {plan.explicacion_interaccion.length > 0 && (
              <ul className="mt-2 list-disc space-y-1 pl-5 text-[12.5px] leading-snug text-ink">
                {plan.explicacion_interaccion.map((line, i) => (
                  <li key={i}>{line}</li>
                ))}
              </ul>
            )}
          </section>
        )}

        {plan.datos_que_faltan.length > 0 && (
          <section aria-labelledby="opt-faltan" className="rounded-xl border border-line bg-surface px-4 py-3">
            <h3 id="opt-faltan" className="text-[13.5px] font-semibold text-ink">
              Faltan datos que pueden cambiar el plan
            </h3>
            <p className="mt-0.5 text-[12.5px] leading-snug text-ink-soft">Complétalos y recalcula: con ellos el plan puede ser otro.</p>
            <BotonesCompletar names={plan.datos_que_faltan} onCompletar={completar} />
          </section>
        )}

        {plan.palancas.length > 0 && (
          <section aria-labelledby="opt-que-hacer">
            <h3 id="opt-que-hacer" className="mb-2 text-[11px] font-semibold uppercase tracking-[0.12em] text-muted">
              Qué hacer
            </h3>
            {plan.palancas.length > 1 && (
              <p className="mb-2.5 text-[12.5px] leading-snug text-ink-soft">
                «Ahorro dentro del plan» es lo que pagarías de más si quitaras solo esa medida. Estas cifras tampoco se suman entre sí: el ahorro del plan completo es{" "}
                {eur(plan.ahorro_total)}.
              </p>
            )}
            <ol className="space-y-3">
                {plan.palancas.map((p, index) => (
                  <PalancaCard
                    key={p.id}
                    p={p}
                    index={index}
                    caseId={caseId}
                    open={sec(`req-${p.id}`)}
                    onToggle={() => toggle(`req-${p.id}`)}
                    onRecalcular={() => void acciones.calcular()}
                  />
                ))}
              </ol>
          </section>
        )}

        {plan.oportunidades.length > 0 && (
          <section aria-labelledby="opt-oportunidades" className="rounded-xl border border-grounding-cited-bd bg-grounding-cited-bg px-4 py-3">
            <h3 id="opt-oportunidades" className="text-[13.5px] font-semibold text-grounding-cited-fg">
              Oportunidades
            </h3>
            <ul className="mt-1 list-disc space-y-1 pl-5 text-[12.5px] leading-snug text-ink-soft">
              {plan.oportunidades.map((o, i) => (
                <li key={i}>
                  <Quoted text={o} />
                </li>
              ))}
            </ul>
          </section>
        )}

        {plan.no_disponibles.length > 0 && (
          <section aria-labelledby="opt-no-disponibles">
            <h3 id="opt-no-disponibles" className="mb-2 text-[11px] font-semibold uppercase tracking-[0.12em] text-muted">
              Lo que no se ha podido usar
            </h3>
            <ul className="space-y-2">
                {plan.no_disponibles.map((nd) => (
                  <NoDisponible key={`${nd.id}-${nd.causa}`} nd={nd} onCompletar={completar} />
                ))}
              </ul>
          </section>
        )}

        {plan.cambios_traza.length > 0 && (
          <Collapsible open={sec("cambios", true)} onToggle={() => toggle("cambios", true)} title="Qué cambia en tu declaración">
            <table className="w-full text-[12.5px]">
              <caption className="sr-only">Líneas del cálculo que cambian, sumadas para el hogar</caption>
              <thead>
                <tr className="text-left text-[11px] uppercase tracking-[0.08em] text-muted">
                  <th scope="col" className="pb-1 pr-2 font-semibold">
                    Concepto
                  </th>
                  <th scope="col" className="pb-1 pr-2 text-right font-semibold">
                    Ahora
                  </th>
                  <th scope="col" className="pb-1 text-right font-semibold">
                    Con el plan
                  </th>
                </tr>
              </thead>
              <tbody>
                {plan.cambios_traza.map((c, i) => (
                  <tr key={i} className="border-t border-line align-top">
                    <td className="py-1.5 pr-2 text-ink-soft [overflow-wrap:anywhere]">
                      {c.concepto}
                      {c.referencia && <span className="block text-[11px] text-muted">{c.referencia}</span>}
                    </td>
                    <td className="whitespace-nowrap py-1.5 pr-2 text-right font-mono tabular-nums text-ink-soft">{eur(c.antes)}</td>
                    <td className="whitespace-nowrap py-1.5 text-right font-mono font-semibold tabular-nums text-ink">{eur(c.despues)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {plan.con_conyuge && <p className="mt-2 text-[11.5px] leading-snug text-muted">Con dos declaraciones, cada línea es la suma de las dos.</p>}
          </Collapsible>
        )}

        {plan.supuestos.length > 0 && (
          <Collapsible tone="warn" open={sec("supuestos", true)} onToggle={() => toggle("supuestos", true)} title={`Lo que se ha supuesto (${plan.supuestos.length})`}>
            <ul className="list-disc space-y-1.5 pl-4 text-[12.5px] leading-snug text-ink-soft">
              {plan.supuestos.map((s, i) => (
                <li key={i}>
                  <Quoted text={s} />
                </li>
              ))}
            </ul>
          </Collapsible>
        )}

        {plan.avisos.length > 0 && (
          <Collapsible tone="warn" open={sec("avisos", true)} onToggle={() => toggle("avisos", true)} title={`Qué debes saber de este cálculo (${plan.avisos.length})`}>
            <ul className="list-disc space-y-1.5 pl-4 text-[12.5px] leading-snug text-ink-soft">
              {plan.avisos.map((a, i) => (
                <li key={i}>
                  <Quoted text={a} />
                </li>
              ))}
            </ul>
          </Collapsible>
        )}

        {plan.descartadas.length > 0 && (
          <Collapsible open={sec("descartadas")} onToggle={() => toggle("descartadas")} title={`Por qué no te propongo… (${plan.descartadas.length})`}>
            <ul className="space-y-2.5">
              {plan.descartadas.map((d) => (
                <li key={d.id} className="text-[12.5px] leading-snug">
                  <p className="font-semibold text-ink">{d.nombre}</p>
                  <p className="text-ink-soft">{d.motivo}</p>
                  <p className="text-[11.5px] text-muted">{d.referencia}</p>
                </li>
              ))}
            </ul>
          </Collapsible>
        )}

        <Collapsible open={sec("explicacion")} onToggle={() => toggle("explicacion")} title="La explicación completa">
          <p className="whitespace-pre-line text-[12.5px] leading-relaxed text-ink-soft">{plan.explicacion}</p>
        </Collapsible>

        {(plan.textos_legales.length > 0 || plan.citas.length > 0) && (
          <Collapsible
            open={sec("fuentes")}
            onToggle={() => toggle("fuentes")}
            title={
              <span className="inline-flex items-center gap-1.5">
                <BookOpen size={14} aria-hidden />
                Textos legales y fuentes
              </span>
            }
          >
            {plan.textos_legales.length > 0 && (
              <ul className="space-y-2.5">
                {plan.textos_legales.map((t) => {
                  const url = boeUrl(t.boe_id);
                  return (
                    <li key={t.referencia} className="text-[12.5px] leading-snug">
                      <p className="font-semibold text-ink">
                        {t.referencia}{" "}
                        {url ? (
                          <a href={url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-0.5 font-normal text-primary-dark underline underline-offset-2 hover:no-underline">
                            {t.boe_id}
                            <ExternalLink size={11} aria-hidden />
                            <span className="sr-only"> (se abre en una pestaña nueva)</span>
                          </a>
                        ) : (
                          <span className="font-normal text-muted">{t.boe_id}</span>
                        )}
                      </p>
                      <blockquote className="mt-0.5 border-l-2 border-line pl-2.5 italic text-ink-soft">«{t.texto}»</blockquote>
                    </li>
                  );
                })}
              </ul>
            )}
            {plan.citas.length > 0 && (
              <>
                <p className="mb-1 mt-3 text-[11px] font-semibold uppercase tracking-[0.1em] text-muted">Referencias del cálculo</p>
                <ul className="list-disc space-y-1 pl-4 text-[12px] leading-snug text-ink-soft">
                  {plan.citas.map((c, i) => (
                    <li key={i} className="[overflow-wrap:anywhere]">
                      {c}
                    </li>
                  ))}
                </ul>
              </>
            )}
          </Collapsible>
        )}

        <Collapsible open={sec("calculo")} onToggle={() => toggle("calculo")} title="El cálculo completo de cada declaración">
          <div className="space-y-4">
            {plan.base.declaraciones.map((d) => (
              <Trace key={`base-${d.titular}`} declaracion={d} title={`Ahora · ${declaracionTitulo(plan.base, d)}`} />
            ))}
            {plan.optimo.declaraciones.map((d) => (
              <Trace key={`opt-${d.titular}`} declaracion={d} title={`Con el plan · ${declaracionTitulo(plan.optimo, d)}`} />
            ))}
          </div>
        </Collapsible>

        {puedeUsarEnSimulador && plan.palancas.length > 0 && (
          <div className="rounded-xl border border-line bg-surface px-4 py-3">
            {simulador.ok ? (
              <>
                <button
                  type="button"
                  onClick={sendToSimulator}
                  disabled={stale || loading}
                  className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-3 py-2 text-[13px] font-semibold text-white transition hover:bg-primary-hover disabled:cursor-not-allowed disabled:opacity-50"
                >
                  Usar este plan en el simulador
                  <ArrowRight size={14} aria-hidden />
                </button>
                <p className="mt-1.5 text-[12px] leading-snug text-muted">
                  {stale ? "Recalcula el plan antes de llevarlo al simulador." : "Abre el simulador con las cifras del plan, para ver la declaración con detalle o probar variaciones."}
                </p>
              </>
            ) : (
              <p className="flex gap-1.5 text-[12.5px] leading-snug text-ink-soft">
                <Info size={14} aria-hidden className="mt-0.5 flex-shrink-0 text-muted" />
                {simulador.motivo}
              </p>
            )}
          </div>
        )}

        <p className="text-[11.5px] leading-relaxed text-muted [overflow-wrap:anywhere]">
          Cada cifra sale de la calculadora del IRPF con los parámetros del ejercicio {plan.ejercicio}
          {plan.optimo.declaraciones[0] ? `: ${plan.optimo.declaraciones[0].resultado.fuente}` : ""}.
        </p>
      </div>
    </div>
  );
}
