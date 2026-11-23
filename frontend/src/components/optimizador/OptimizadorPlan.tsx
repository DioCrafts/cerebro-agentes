// «Tu plan»: the best combination of the choices the law leaves to the household, as the
// optimiser computed it. Every figure is the plan's; standalone savings are never added up.

import { clsx } from "clsx";
import type { RefObject } from "react";

import type { PalancaNoDisponible } from "../../apiOptimizador";
import { FIELD_BY_NAME } from "../../lib/optimizador/campos";
import { CAUSA_LABEL, boeUrl, declaracionTitulo, enMinuscula, eur, joinList, missingLabel, planEnSimulador, resultadoTexto, tributacionTexto } from "../../lib/optimizador/textos";
import { formatDate } from "../../lib/presentation";
import { Blankslate, Button, Caja, Flash, Label, Link } from "../../ui";
import { ArrowRightIcon, BookIcon, GraphIcon, InfoIcon, LinkExternalIcon, SyncIcon } from "../../ui/iconos";
import { CitaLegal, Plegable, TablaCifras } from "../calculos/piezas";
import { PalancaCard } from "./PalancaCard";
import { Quoted, Trace } from "./piezas";
import type { Optimizador } from "./useOptimizador";

interface Props {
  o: Optimizador;
  caseId: string | null;
  puedeUsarEnSimulador: boolean;
  headlineRef: RefObject<HTMLElement | null>;
}

function BotonesCompletar({ names, onCompletar }: { names: string[]; onCompletar: (name: string) => void }) {
  return (
    <div className="flex flex-wrap gap-2">
      {names.map((name) => {
        const inForm = name === "conyuge" || name === "estado_civil" || name === "comunidad_autonoma" || name === "perdidas_latentes" || FIELD_BY_NAME.has(name);
        return (
          <Button key={name} size="small" trailingVisual={ArrowRightIcon} onClick={() => onCompletar(name)}>
            {inForm ? `Completar: ${enMinuscula(missingLabel(name))}` : `${missingLabel(name)}: abrir el simulador`}
          </Button>
        );
      })}
    </div>
  );
}

function NoDisponible({ nd, onCompletar }: { nd: PalancaNoDisponible; onCompletar: (name: string) => void }) {
  return (
    <Caja.Fila className="flex flex-col gap-2 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <p className="m-0 font-semibold">{nd.nombre}</p>
        <Label variant={nd.causa === "faltan_datos" ? "accent" : "secondary"}>{CAUSA_LABEL[nd.causa]}</Label>
      </div>
      <p className="m-0">
        <Quoted text={nd.detalle} />
      </p>
      {nd.datos_que_faltan.length > 0 && <BotonesCompletar names={nd.datos_que_faltan} onCompletar={onCompletar} />}
    </Caja.Fila>
  );
}

function Lista({ items }: { items: string[] }) {
  return (
    <ul className="m-0 flex list-disc flex-col gap-1 pl-5">
      {items.map((s, i) => (
        <li key={i}>
          <Quoted text={s} />
        </li>
      ))}
    </ul>
  );
}

export function OptimizadorPlan({ o, caseId, puedeUsarEnSimulador, headlineRef }: Props) {
  const { state, stale, acciones } = o;
  const { computed, loading } = state;
  const { goToData, completar, sendToSimulator, section: sec, toggleSection: toggle } = acciones;
  if (!computed) {
    return (
      <Blankslate border>
        <Blankslate.Visual>
          <GraphIcon size={24} />
        </Blankslate.Visual>
        <Blankslate.Heading as="h3">{loading ? "Calculando tu plan…" : "Aún no has calculado tu plan"}</Blankslate.Heading>
        {!loading && (
          <>
            <Blankslate.Description>Revisa tus datos y pulsa «Calcular mi plan».</Blankslate.Description>
            <Blankslate.PrimaryAction onClick={goToData}>Ir a tus datos</Blankslate.PrimaryAction>
          </>
        )}
      </Blankslate>
    );
  }
  const { plan } = computed;
  const ahorro = Number(plan.ahorro_total);
  const interaccion = Number(plan.interaccion);
  const diferidas = plan.palancas.filter((p) => p.naturaleza === "diferimiento");
  const gastos = plan.palancas.filter((p) => p.naturaleza === "requiere_gastar");
  const simulador = planEnSimulador(plan, () => 0);
  return (
    <div className="flex flex-col gap-4">
      {stale && (
        <Flash variant="warning" role="status" className="sticky top-0 z-10">
          <p className="m-0 font-semibold">Plan desactualizado: has cambiado datos desde que se calculó.</p>
          <div className="mt-2 flex flex-wrap gap-2">
            <Button size="small" variant="primary" leadingVisual={SyncIcon} onClick={() => void acciones.calcular()} loading={loading}>
              Recalcular
            </Button>
            <Button size="small" onClick={goToData}>
              Ver mis datos
            </Button>
          </div>
        </Flash>
      )}

      <div className={clsx("flex flex-col gap-4 transition-opacity", (stale || loading) && "opacity-50")} aria-busy={loading || undefined}>
        <section ref={headlineRef} tabIndex={-1} aria-labelledby="opt-headline" className="scroll-mt-4 rounded-medium outline-none focus-visible:shadow-resting-md">
        <Caja>
          <Caja.Cabecera>
            <span className="flex-1 text-sm font-semibold">
              Tu plan para el IRPF {plan.ejercicio}
              {stale && <span className="font-normal text-fg-attention"> · desactualizado</span>}
            </span>
          </Caja.Cabecera>
          <Caja.Cuerpo className="flex flex-col gap-3 text-sm">
            <div>
              <h3 id="opt-headline" className={clsx("m-0 font-semibold", ahorro > 0 ? "text-3xl text-fg-success" : "text-xl")}>
                {ahorro > 0 ? `Puedes ahorrar ${eur(plan.ahorro_total)}` : "Con estos datos no hay nada que mejorar"}
              </h3>
              <p className="m-0 mt-1 text-fg-muted">
                {ahorro > 0
                  ? `en tu IRPF de ${plan.ejercicio}, frente a lo que pagarías ahora`
                  : plan.fase === "solo_opciones_de_declaracion"
                    ? "El ejercicio ya ha terminado y ninguna opción de la declaración lo reduce."
                    : "Ninguna combinación de las opciones disponibles reduce tu impuesto."}
              </p>
            </div>
            <dl className="m-0 grid gap-3 sm:grid-cols-2">
              {(
                [
                  ["Ahora", plan.base],
                  ["Con el plan", plan.optimo],
                ] as const
              ).map(([titulo, hogar]) => (
                <div key={titulo} className="rounded-medium border border-border-muted bg-canvas-muted px-3 py-2">
                  <dt className="text-xs text-fg-muted">{titulo}</dt>
                  <dd className="m-0 font-semibold tabular-nums">{resultadoTexto(hogar.resultado_total)}</dd>
                  <dd className="m-0 text-xs text-fg-muted">{tributacionTexto(hogar)}</dd>
                </div>
              ))}
            </dl>
            {(diferidas.length > 0 || gastos.length > 0) && (
              <Flash variant="warning">
                <strong>No todo es ahorro definitivo.</strong>
                {diferidas.length > 0 && (
                  <>
                    {" "}
                    {joinList(diferidas.map((p) => `«${p.nombre}»`))} {diferidas.length === 1 ? "es un diferimiento" : "son diferimientos"}: pagas menos este año, pero ese impuesto puede
                    llegar más adelante.
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
              </Flash>
            )}
            {plan.fase === "solo_opciones_de_declaracion" && ahorro > 0 && <p className="m-0">El ejercicio ya ha terminado: solo quedan las opciones de la declaración.</p>}
            <p className="m-0 text-xs text-fg-muted">
              {plan.busqueda.metodo === "exhaustiva"
                ? `Búsqueda exhaustiva: ${plan.busqueda.evaluaciones.toLocaleString("es-ES")} cálculos de la declaración, todas las combinaciones de candidatos. Plan para el ${formatDate(plan.fecha)}.`
                : `Plan para el ${formatDate(plan.fecha)}.`}
            </p>
          </Caja.Cuerpo>
        </Caja>
        </section>

        {plan.busqueda.metodo === "heuristica" && (
          <Flash variant="warning" role="note">
            <strong>Búsqueda aproximada ({plan.busqueda.evaluaciones.toLocaleString("es-ES")} cálculos).</strong> {plan.busqueda.nota}
          </Flash>
        )}

        {plan.palancas.length > 1 && (interaccion !== 0 || plan.explicacion_interaccion.length > 0) && (
          <Caja as="section" variante="acento" aria-labelledby="opt-interaccion">
            <Caja.Cabecera>
              <h3 id="opt-interaccion" className="m-0 flex-1 text-sm font-semibold">
                {interaccion > 0
                  ? `Juntas valen ${eur(interaccion)} más que por separado`
                  : interaccion < 0
                    ? `Juntas valen ${eur(-interaccion)} menos que por separado`
                    : "Cómo se combinan las medidas"}
              </h3>
            </Caja.Cabecera>
            <Caja.Cuerpo className="flex flex-col gap-2 text-sm">
              <p className="m-0">
                Las medidas se influyen entre sí, así que sus ahorros no se suman. Calculada cada una por separado, la suma daría {eur(plan.suma_ahorros_aislados)}; eso <strong>no</strong> es lo
                que ahorras. El ahorro real del plan es {eur(plan.ahorro_total)}.
              </p>
              {plan.explicacion_interaccion.length > 0 && (
                <ul className="m-0 flex list-disc flex-col gap-1 pl-5">
                  {plan.explicacion_interaccion.map((line, i) => (
                    <li key={i}>{line}</li>
                  ))}
                </ul>
              )}
            </Caja.Cuerpo>
          </Caja>
        )}

        {plan.datos_que_faltan.length > 0 && (
          <Caja as="section" aria-labelledby="opt-faltan">
            <Caja.Cabecera titulo={<span id="opt-faltan">Faltan datos que pueden cambiar el plan</span>} />
            <Caja.Cuerpo className="flex flex-col gap-2 text-sm">
              <p className="m-0 text-fg-muted">Complétalos y recalcula: con ellos el plan puede ser otro.</p>
              <BotonesCompletar names={plan.datos_que_faltan} onCompletar={completar} />
            </Caja.Cuerpo>
          </Caja>
        )}

        {plan.palancas.length > 0 && (
          <section aria-labelledby="opt-que-hacer" className="flex flex-col gap-2">
            <h3 id="opt-que-hacer" className="m-0 text-base font-semibold">
              Qué hacer
            </h3>
            {plan.palancas.length > 1 && (
              <p className="m-0 text-sm text-fg-muted">
                «Ahorro dentro del plan» es lo que pagarías de más si quitaras solo esa medida. Estas cifras tampoco se suman entre sí: el ahorro del plan completo es {eur(plan.ahorro_total)}.
              </p>
            )}
            <ol className="m-0 flex list-none flex-col gap-3 p-0">
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
          <Flash variant="success" role="region" aria-labelledby="opt-oportunidades">
            <h3 id="opt-oportunidades" className="m-0 mb-1 text-sm font-semibold">
              Oportunidades
            </h3>
            <Lista items={plan.oportunidades} />
          </Flash>
        )}

        {plan.no_disponibles.length > 0 && (
          <Caja as="section" aria-labelledby="opt-no-disponibles">
            <Caja.Cabecera titulo={<span id="opt-no-disponibles">Lo que no se ha podido usar</span>} />
            <Caja.Lista>
              {plan.no_disponibles.map((nd) => (
                <NoDisponible key={`${nd.id}-${nd.causa}`} nd={nd} onCompletar={completar} />
              ))}
            </Caja.Lista>
          </Caja>
        )}

        {plan.cambios_traza.length > 0 && (
          <Plegable sinRelleno abierto={sec("cambios", true)} onAlternar={() => toggle("cambios", true)} titulo="Qué cambia en tu declaración">
            <TablaCifras
              titulo="Líneas del cálculo que cambian, sumadas para el hogar"
              columnas={["Concepto", "Ahora", "Con el plan"]}
              filas={plan.cambios_traza.map((c) => ({ concepto: c.concepto, referencia: c.referencia, cifras: [eur(c.antes), <strong key="d">{eur(c.despues)}</strong>] }))}
            />
            {plan.con_conyuge && <p className="m-0 px-4 pb-3 text-xs text-fg-muted">Con dos declaraciones, cada línea es la suma de las dos.</p>}
          </Plegable>
        )}

        {plan.supuestos.length > 0 && (
          <Plegable tono="aviso" abierto={sec("supuestos", true)} onAlternar={() => toggle("supuestos", true)} titulo={`Lo que se ha supuesto (${plan.supuestos.length})`}>
            <Lista items={plan.supuestos} />
          </Plegable>
        )}

        {plan.avisos.length > 0 && (
          <Plegable tono="aviso" abierto={sec("avisos", true)} onAlternar={() => toggle("avisos", true)} titulo={`Qué debes saber de este cálculo (${plan.avisos.length})`}>
            <Lista items={plan.avisos} />
          </Plegable>
        )}

        {plan.descartadas.length > 0 && (
          <Plegable abierto={sec("descartadas")} onAlternar={() => toggle("descartadas")} titulo={`Por qué no te propongo… (${plan.descartadas.length})`}>
            <ul className="m-0 flex list-none flex-col gap-3 p-0">
              {plan.descartadas.map((d) => (
                <li key={d.id}>
                  <p className="m-0 font-semibold">{d.nombre}</p>
                  <p className="m-0">{d.motivo}</p>
                  <p className="m-0 text-xs text-fg-muted">{d.referencia}</p>
                </li>
              ))}
            </ul>
          </Plegable>
        )}

        <Plegable abierto={sec("explicacion")} onAlternar={() => toggle("explicacion")} titulo="La explicación completa">
          <p className="m-0 whitespace-pre-line">{plan.explicacion}</p>
        </Plegable>

        {(plan.textos_legales.length > 0 || plan.citas.length > 0) && (
          <Plegable
            abierto={sec("fuentes")}
            onAlternar={() => toggle("fuentes")}
            titulo={
              <span className="inline-flex items-center gap-2">
                <BookIcon className="text-fg-muted" />
                Textos legales y fuentes
              </span>
            }
          >
            {plan.textos_legales.length > 0 && (
              <ul className="m-0 flex list-none flex-col gap-3 p-0">
                {plan.textos_legales.map((t) => {
                  const url = boeUrl(t.boe_id);
                  return (
                    <li key={t.referencia} className="flex flex-col gap-1">
                      <p className="m-0 font-semibold">
                        {t.referencia}{" "}
                        {url ? (
                          <Link href={url} target="_blank" rel="noreferrer" className="font-normal">
                            {t.boe_id}
                            <LinkExternalIcon size={12} className="ml-1" />
                            <span className="sr-only"> (se abre en una pestaña nueva)</span>
                          </Link>
                        ) : (
                          <span className="font-normal text-fg-muted">{t.boe_id}</span>
                        )}
                      </p>
                      <CitaLegal>«{t.texto}»</CitaLegal>
                    </li>
                  );
                })}
              </ul>
            )}
            {plan.citas.length > 0 && (
              <>
                <p className="m-0 mb-1 mt-3 text-xs font-semibold text-fg-muted">Referencias del cálculo</p>
                <ul className="m-0 flex list-disc flex-col gap-1 pl-5 text-xs">
                  {plan.citas.map((c, i) => (
                    <li key={i} className="wrap-anywhere">
                      {c}
                    </li>
                  ))}
                </ul>
              </>
            )}
          </Plegable>
        )}

        <Plegable sinRelleno abierto={sec("calculo")} onAlternar={() => toggle("calculo")} titulo="El cálculo completo de cada declaración">
          <div className="flex flex-col divide-y divide-border-default">
            {plan.base.declaraciones.map((d) => (
              <Trace key={`base-${d.titular}`} declaracion={d} title={`Ahora · ${declaracionTitulo(plan.base, d)}`} />
            ))}
            {plan.optimo.declaraciones.map((d) => (
              <Trace key={`opt-${d.titular}`} declaracion={d} title={`Con el plan · ${declaracionTitulo(plan.optimo, d)}`} />
            ))}
          </div>
        </Plegable>

        {puedeUsarEnSimulador && plan.palancas.length > 0 && (
          <Caja>
            <Caja.Cuerpo className="flex flex-col gap-2 text-sm">
              {simulador.ok ? (
                <>
                  <div>
                    <Button variant="primary" trailingVisual={ArrowRightIcon} onClick={sendToSimulator} disabled={stale || loading}>
                      Usar este plan en el simulador
                    </Button>
                  </div>
                  <p className="m-0 text-xs text-fg-muted">
                    {stale ? "Recalcula el plan antes de llevarlo al simulador." : "Abre el simulador con las cifras del plan, para ver la declaración con detalle o probar variaciones."}
                  </p>
                </>
              ) : (
                <p className="m-0 flex gap-2">
                  <InfoIcon className="mt-0.5 shrink-0 text-fg-muted" />
                  {simulador.motivo}
                </p>
              )}
            </Caja.Cuerpo>
          </Caja>
        )}

        <p className="m-0 text-xs text-fg-muted wrap-anywhere">
          Cada cifra sale de la calculadora del IRPF con los parámetros del ejercicio {plan.ejercicio}
          {plan.optimo.declaraciones[0] ? `: ${plan.optimo.declaraciones[0].resultado.fuente}` : ""}.
        </p>
      </div>
    </div>
  );
}
