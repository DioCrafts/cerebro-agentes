// «Planificar el hogar»: what to do with a sum of money — repay the mortgage early (term or
// instalment), contribute to pension plans, donate within a budget or keep it liquid —, from the
// deterministic planner behind POST /planificar. No model call: every figure is the plan's; the
// screen never adds up figures of different modules by itself.
//
// Two tabs: «Tu dinero» (amount, horizon, cushion, the alternative return the user assumes and the
// levers they rule out) and «Tu plan» (allocation, net position in both views of the pension, each
// lever inside the plan and alone, scenarios, aids, assumptions and what is not modelled). Nothing is
// stored: the inputs live in this screen only.
//
// Shown as a page (registro.ts: presentacion "pagina"): the shell puts the title and the frame.

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
import { Blankslate, Button, Caja, Checkbox, CheckboxGroup, Flash, FormControl, Label, Link, Radio, RadioGroup, VisuallyHidden } from "../ui";
import { AlertIcon, CheckIcon, LawIcon, ProjectRoadmapIcon } from "../ui/iconos";
import { CampoCasilla, CampoCifra, CampoSelector, CitaLegal, Plegable, SeccionFormulario, TablaCifras } from "./calculos/piezas";
import { Pestanas } from "./calculos/Pestanas";

type Tab = "datos" | "plan";

interface PlanificadorPanelProps {
  caseId?: string | null;
  taxPeriod?: string | null;
}

type CampoCifraPlan = "importe" | "excedente" | "rentabilidad" | "colchon" | "gastos" | "meses" | "liquidez" | "rescate" | "donativo" | "presupuesto" | "parteConyuge";

const OPCIONES_HORIZONTE = HORIZONTES.map((h) => ({ value: String(h), label: `${h} ${h === 1 ? "año" : "años"}` }));
const OPCIONES_VISTA = [
  { value: "auto", label: "Automático" },
  { value: "diferimiento", label: "Diferimiento (ahorro de hoy)" },
  { value: "definitivo", label: "Ahorro definitivo (descontando el rescate)" },
];
const MODOS_COLCHON: Array<[FormularioPlan["colchonModo"], string]> = [
  ["meses", "Meses de gastos"],
  ["importe", "Una cantidad"],
  ["ninguno", "Sin colchón"],
];

function Cita({ texto }: { texto: string }) {
  return (
    <>
      {splitQuotes(texto).map((part, i) => (part.quote ? <em key={i}>«{part.text}»</em> : <span key={i}>{part.text}</span>))}
    </>
  );
}

/** A section of the plan: a box with its title in the header. */
function Seccion({ id, titulo, children, lista = false }: { id: string; titulo: string; children: ReactNode; lista?: boolean }) {
  return (
    <Caja as="section" aria-labelledby={id}>
      <Caja.Cabecera titulo={<span id={id}>{titulo}</span>} />
      {lista ? children : <Caja.Cuerpo className="text-sm">{children}</Caja.Cuerpo>}
    </Caja>
  );
}

export function PlanificadorPanel({ caseId, taxPeriod }: PlanificadorPanelProps) {
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

  useEffect(() => () => abortRef.current?.abort(), []);

  const set = <K extends keyof FormularioPlan>(campo: K, valor: FormularioPlan[K]) => setForm((f) => ({ ...f, [campo]: valor }));
  /** What every figure of the form shares: its id (`plan-<campo>`), value, setter and validation (after the first send). */
  const cifra = (campo: CampoCifraPlan) => ({
    id: `plan-${campo}`,
    valor: form[campo],
    onCambio: (valor: string) => set(campo, valor),
    error: enviado ? problemas[campo] : undefined,
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
      <form onSubmit={calcular} noValidate className="flex flex-col gap-6">
        <p className="m-0 text-sm text-fg-muted">
          Con tus cifras del expediente, la hipoteca y las ayudas (si tienes esos módulos activados), el planificador prueba todos los repartos y te dice cuál deja mejor a tu hogar al
          final del plazo que elijas.
        </p>
        <SeccionFormulario titulo="Tu dinero">
          <CampoCifra {...cifra("importe")} etiqueta="Dinero que puedes destinar ahora" />
          <CampoCifra {...cifra("excedente")} etiqueta="Excedente mensual" ayuda="Lo que ahorras cada mes hasta diciembre (opcional)" />
          <CampoSelector
            id="plan-horizonte"
            etiqueta="Horizonte"
            valor={String(form.horizonte)}
            onCambio={(v) => set("horizonte", Number(v))}
            opciones={OPCIONES_HORIZONTE}
            ayuda="Años hasta los que se compara"
          />
          <CampoCifra
            {...cifra("rentabilidad")}
            tipo="porcentaje"
            etiqueta="Rentabilidad alternativa (anual neta)"
            placeholder="0"
            ayuda="Lo que te daría el dinero que no uses. Es tu supuesto: no es una recomendación"
          />
        </SeccionFormulario>

        <SeccionFormulario titulo="Colchón de emergencia">
          <div className="sm:col-span-2">
            <RadioGroup name="plan-colchon-modo">
              <RadioGroup.Label visuallyHidden>Cómo quieres fijar el colchón</RadioGroup.Label>
              <div className="flex flex-wrap gap-x-6 gap-y-2">
                {MODOS_COLCHON.map(([modo, label]) => (
                  <FormControl key={modo}>
                    <Radio value={modo} checked={form.colchonModo === modo} onChange={() => set("colchonModo", modo)} />
                    <FormControl.Label className="font-normal">{label}</FormControl.Label>
                  </FormControl>
                ))}
              </div>
            </RadioGroup>
          </div>
          {form.colchonModo === "meses" && (
            <>
              <CampoCifra {...cifra("gastos")} etiqueta="Gastos del hogar al mes" />
              <CampoCifra {...cifra("meses")} tipo="numero" etiqueta="Meses de colchón" />
            </>
          )}
          {form.colchonModo === "importe" && <CampoCifra {...cifra("colchon")} etiqueta="Colchón que quieres mantener" />}
          {form.colchonModo !== "ninguno" && <CampoCifra {...cifra("liquidez")} etiqueta="Ahorros líquidos que ya tienes aparte" ayuda="Cuentan para el colchón" />}
        </SeccionFormulario>

        <SeccionFormulario titulo="Palancas">
          <div className="sm:col-span-2">
            <CheckboxGroup>
              <CheckboxGroup.Label>Lo que se puede proponer</CheckboxGroup.Label>
              <CheckboxGroup.Caption>Desmarca las que no quieras que se propongan.</CheckboxGroup.Caption>
              <div className="grid gap-2 sm:grid-cols-2">
                {PALANCAS.map((p) => (
                  <FormControl key={p.id}>
                    <Checkbox value={p.id} checked={!form.excluidas.includes(p.id)} onChange={() => toggleExcluida(p.id)} />
                    <FormControl.Label className="font-normal">{p.label}</FormControl.Label>
                  </FormControl>
                ))}
              </div>
            </CheckboxGroup>
          </div>
          <CampoCasilla
            etiqueta="No proponer nada que me haga perder o rebajar una ayuda pública que hoy me corresponde"
            marcada={form.protegerAyudas}
            onCambio={(v) => set("protegerAyudas", v)}
          />
        </SeccionFormulario>

        <Plegable titulo="Pensiones, donativo e hipoteca compartida">
          <div className="grid gap-4 sm:grid-cols-2">
            <CampoCifra {...cifra("rescate")} tipo="porcentaje" etiqueta="Tipo al rescatar el plan" ayuda="Tu tipo marginal esperado en la jubilación" />
            <CampoSelector
              id="plan-vista"
              etiqueta="Valorar el plan de pensiones como"
              valor={form.vista}
              onCambio={(v) => set("vista", v as FormularioPlan["vista"])}
              opciones={OPCIONES_VISTA}
              ayuda="Las dos vistas se muestran siempre"
            />
            <CampoCifra {...cifra("donativo")} etiqueta="Donativo que quieres hacer" />
            <CampoCifra {...cifra("presupuesto")} etiqueta="Máximo que donarías" />
            <CampoCifra {...cifra("parteConyuge")} tipo="porcentaje" etiqueta="Parte de la hipoteca que paga tu cónyuge" ayuda="Para la deducción por vivienda anterior a 2013" />
          </div>
        </Plegable>

        <div className="border-t border-border-default pt-4">
          <Button type="submit" variant="primary" leadingVisual={ProjectRoadmapIcon} loading={loading}>
            Calcular el mejor reparto
          </Button>
        </div>
      </form>
    );
  }

  function planTab() {
    if (!computed) {
      if (error) return null;
      return (
        <Blankslate border>
          <Blankslate.Visual>
            <ProjectRoadmapIcon size={24} />
          </Blankslate.Visual>
          <Blankslate.Heading as="h3">Aún no hay plan</Blankslate.Heading>
          <Blankslate.Description>Rellena «Tu dinero» y pulsa «Calcular el mejor reparto».</Blankslate.Description>
          <Blankslate.PrimaryAction onClick={() => setTab("datos")}>Ir a tu dinero</Blankslate.PrimaryAction>
        </Blankslate>
      );
    }
    const { plan } = computed;
    const v = plan.valoracion;
    const otraVista = plan.vista === "diferimiento" ? "ahorro definitivo" : "diferimiento";
    const otraTotal = plan.vista === "diferimiento" ? v.total_definitivo : v.total_diferimiento;
    return (
      <div className="flex flex-col gap-4">
        {stale && <Flash variant="warning">Has cambiado los datos: este plan es del cálculo anterior. Vuelve a calcular.</Flash>}

        <Caja as="section" variante="acento" aria-labelledby="plan-titular">
          <Caja.Cuerpo className="flex flex-col gap-1">
            <h3 id="plan-titular" ref={headlineRef} tabIndex={-1} className="m-0 text-xl font-semibold outline-none">
              Posición neta en {plan.horizonte_anos} {plan.horizonte_anos === 1 ? "año" : "años"}: <span className="tabular-nums">{eur(v.total)}</span>
            </h3>
            <p className="m-0 text-sm text-fg-muted">
              Frente a dejar {eur(plan.importe_total)} líquidos al {plan.rentabilidad_alternativa_pct.replace(".", ",")} % anual, hasta el {formatDate(plan.fecha_horizonte)}.{" "}
              {plan.vista === "diferimiento" ? "Contando el plan de pensiones como diferimiento" : "Descontando el impuesto al rescatar el plan"}; en la vista de {otraVista}:{" "}
              {eur(otraTotal)}. En euros de hoy: {eur(v.valor_actual)}.
            </p>
          </Caja.Cuerpo>
        </Caja>

        <Seccion id="plan-reparto" titulo="Reparto" lista>
          <TablaCifras
            titulo="Reparto del dinero"
            filas={plan.asignacion.map((a) => ({
              concepto: (
                <>
                  <span className="font-semibold">{a.nombre}</span>
                  <span className="block text-xs text-fg-muted">{a.detalle}</span>
                </>
              ),
              cifras: [eur(a.importe)],
            }))}
          />
        </Seccion>

        {plan.palancas.length > 0 && (
          <Seccion id="plan-palancas" titulo="Cada palanca, dentro del plan y sola" lista>
            <Caja.Lista>
              {plan.palancas.map((p) => (
                <Caja.Fila key={p.id} className="flex flex-col gap-2 text-sm">
                  <p className="m-0 flex items-baseline justify-between gap-2">
                    <span className="font-semibold">{p.nombre}</span>
                    <span className="font-mono tabular-nums">{eur(p.importe)}</span>
                  </p>
                  <p className="m-0 text-xs text-fg-muted">{p.naturaleza}</p>
                  <dl className="m-0 grid grid-cols-2 gap-2">
                    <div>
                      <dt className="text-xs text-fg-muted">Dentro del plan</dt>
                      <dd className="m-0 font-semibold tabular-nums">{eur(p.valor_marginal)}</dd>
                    </div>
                    <div>
                      <dt className="text-xs text-fg-muted">Sola (con {eur(p.importe_aislado)})</dt>
                      <dd className="m-0 font-semibold tabular-nums">{eur(p.valor_aislado)}</dd>
                    </div>
                  </dl>
                  <p className="m-0">{p.explicacion}</p>
                  {p.referencias.length > 0 && <p className="m-0 text-xs text-fg-muted">{p.referencias.join(" · ")}</p>}
                </Caja.Fila>
              ))}
            </Caja.Lista>
            <Caja.Pie className="text-sm">
              Por separado suman {eur(plan.suma_aislados)}; el plan, {eur(v.total)} (diferencia {eur(plan.interaccion)}).
            </Caja.Pie>
          </Seccion>
        )}

        {plan.explicacion_interaccion.length > 0 && (
          <Seccion id="plan-interaccion" titulo="Por qué la combinación no es la suma">
            <ul className="m-0 flex list-disc flex-col gap-1 pl-5">
              {plan.explicacion_interaccion.map((x) => (
                <li key={x}>{x}</li>
              ))}
            </ul>
          </Seccion>
        )}

        <Seccion id="plan-cuentas" titulo="Las cuentas, línea a línea" lista>
          <TablaCifras
            titulo="Las cuentas del plan"
            filas={v.componentes.map((c) => ({
              concepto: (
                <>
                  {c.concepto}{" "}
                  <Label variant={NATURALEZA_COMPONENTE[c.naturaleza].variante} size="small">
                    {NATURALEZA_COMPONENTE[c.naturaleza].label}
                  </Label>
                </>
              ),
              referencia: `Módulo ${c.modulo}${c.referencia ? ` · ${c.referencia}` : ""}`,
              cifras: [eur(c.importe)],
            }))}
          />
          <Caja.Pie className="text-sm">
            IRPF de {plan.ejercicio} con el plan: {eur(v.impuesto_ejercicio)} ({v.tributacion.replace("_", " ")}).
          </Caja.Pie>
        </Seccion>

        <Seccion id="plan-escenarios" titulo="Comparado con otras opciones" lista>
          <TablaCifras
            titulo="Escenarios comparados"
            filas={plan.escenarios.map((e) => ({
              concepto: (
                <>
                  {e.nombre}
                  {!e.factible && (
                    <Label variant="danger" size="small" className="ml-2">
                      no respeta tus límites
                    </Label>
                  )}
                </>
              ),
              cifras: [eur(e.total)],
              destacada: e.nombre === "Plan óptimo",
            }))}
          />
        </Seccion>

        {plan.hipoteca && Number(plan.hipoteca.importe) > 0 && (
          <Seccion id="plan-hipoteca" titulo="Tu hipoteca con el plan" lista>
            <TablaCifras
              titulo="Tu hipoteca, antes y con el plan"
              filas={[
                { concepto: "Cuota", cifras: [`${eur(plan.hipoteca.cuota_actual)} → ${plan.hipoteca.cuota_nueva !== null ? eur(plan.hipoteca.cuota_nueva) : "—"}`] },
                { concepto: "Última cuota", cifras: [`${formatDate(plan.hipoteca.fin_actual)} → ${plan.hipoteca.fin_nuevo ? formatDate(plan.hipoteca.fin_nuevo) : "—"}`] },
                { concepto: `Comisión${plan.hipoteca.comision_es_maximo ? " (máximo legal)" : ""}`, cifras: [eur(plan.hipoteca.comision)] },
                { concepto: "Intereses ahorrados (todo el préstamo)", cifras: [eur(plan.hipoteca.intereses_ahorrados_total)] },
              ]}
            />
            {plan.hipoteca.fundamento_comision.length > 0 && (
              <Caja.Pie>
                <ul className="m-0 flex list-disc flex-col gap-1 pl-5 text-xs text-fg-muted">
                  {plan.hipoteca.fundamento_comision.map((x) => (
                    <li key={x}>{x}</li>
                  ))}
                </ul>
              </Caja.Pie>
            )}
          </Seccion>
        )}

        {plan.ayudas.length > 0 && (
          <Seccion id="plan-ayudas" titulo="Ayudas públicas" lista>
            <Caja.Lista>
              {plan.ayudas.map((a) => {
                const cambia = a.estado_antes !== a.estado_despues || a.importe_antes !== a.importe_despues;
                return (
                  <Caja.Fila key={a.id} className="flex flex-col gap-1 text-sm">
                    <p className="m-0 flex flex-wrap items-center justify-between gap-2">
                      <span className="font-semibold">{a.nombre}</span>
                      <Label variant={cambia ? "danger" : "secondary"}>
                        {ESTADO_AYUDA[a.estado_antes] ?? a.estado_antes}
                        {cambia ? ` → ${ESTADO_AYUDA[a.estado_despues] ?? a.estado_despues}` : ""}
                      </Label>
                    </p>
                    <p className="m-0 text-fg-muted">Mide: {a.magnitud}.</p>
                    <p className="m-0">{a.explicacion}</p>
                    <details className="mt-1">
                      <summary className="cursor-pointer text-xs text-fg-accent">La norma</summary>
                      <p className="m-0 mt-1 text-xs text-fg-muted">
                        <Cita texto={a.cita} />
                      </p>
                    </details>
                  </Caja.Fila>
                );
              })}
            </Caja.Lista>
          </Seccion>
        )}

        {plan.no_usadas.length > 0 && (
          <Seccion id="plan-no-usadas" titulo="Lo que no entra en el plan" lista>
            <Caja.Lista>
              {plan.no_usadas.map((n) => (
                <Caja.Fila key={`${n.id}-${n.causa}`} className="flex flex-col gap-1 text-sm">
                  <p className="m-0 flex flex-wrap items-center gap-2">
                    <span className="font-semibold">{n.nombre}</span>
                    <Label variant="secondary">{CAUSA_NO_USADA[n.causa]}</Label>
                  </p>
                  <p className="m-0">{n.detalle}</p>
                </Caja.Fila>
              ))}
            </Caja.Lista>
          </Seccion>
        )}

        {(plan.supuestos.length > 0 || plan.avisos.length > 0) && (
          <Seccion id="plan-supuestos" titulo="Supuestos y avisos">
            <ul className="m-0 flex list-disc flex-col gap-1 pl-5">
              {plan.supuestos.map((s) => (
                <li key={s}>{s}</li>
              ))}
              {plan.avisos.map((s) => (
                <li key={s} className="font-semibold">
                  <AlertIcon size={12} className="mr-1 text-fg-danger" />
                  {s}
                </li>
              ))}
            </ul>
          </Seccion>
        )}

        <Plegable titulo="Lo que no se ha modelado y cómo se ha buscado">
          <ul className="m-0 flex list-disc flex-col gap-1 pl-5">
            {plan.no_modelado.map((s) => (
              <li key={s}>{s}</li>
            ))}
          </ul>
          <p className="m-0 mt-2 text-xs text-fg-muted">
            {plan.busqueda.combinaciones.toLocaleString("es-ES")} combinaciones, paso {eur(plan.busqueda.resolucion)}
            {plan.busqueda.resolucion_hipoteca ? ` (hipoteca: ${eur(plan.busqueda.resolucion_hipoteca)})` : ""}. {plan.busqueda.nota}
          </p>
        </Plegable>

        {plan.textos_legales.length > 0 && (
          <Plegable
            titulo={
              <span className="inline-flex items-center gap-2">
                <LawIcon className="text-fg-muted" />
                Textos legales
              </span>
            }
          >
            <ul className="m-0 flex list-none flex-col gap-3 p-0">
              {plan.textos_legales.map((t) => {
                const url = boeUrl(t.boe_id);
                return (
                  <li key={t.referencia} className="flex flex-col gap-1">
                    <p className="m-0">
                      <span className="font-semibold">{t.referencia}</span>{" "}
                      {url ? (
                        <Link href={url} target="_blank" rel="noreferrer">
                          {t.boe_id}
                        </Link>
                      ) : (
                        t.boe_id
                      )}
                    </p>
                    <CitaLegal>«{t.texto}»</CitaLegal>
                  </li>
                );
              })}
            </ul>
          </Plegable>
        )}
        <p className="m-0 text-xs text-fg-muted">No es una recomendación de inversión: son cuentas con tus supuestos.</p>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      <Pestanas
        etiqueta="Planificador"
        idBase="plan"
        actual={tab}
        onCambiar={setTab}
        pestanas={[
          { id: "datos", etiqueta: "1. Tu dinero" },
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
      <div role="tabpanel" id="plan-tabpanel" aria-labelledby={`plan-tab-${tab}`} className="flex flex-col gap-4">
        {error && tab === "plan" && (
          <Flash variant="danger" role="alert">
            {error}
          </Flash>
        )}
        {loading && <VisuallyHidden role="status">Calculando el mejor reparto…</VisuallyHidden>}
        {tab === "datos" ? datosTab() : planTab()}
      </div>
      <p className="m-0 text-xs text-fg-muted">Cálculo determinista, sin inteligencia artificial. Nada de esta pantalla se guarda.</p>
    </div>
  );
}
