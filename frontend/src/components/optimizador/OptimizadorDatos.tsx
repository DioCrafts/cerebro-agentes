/* ui-legado: pendiente de migrar a src/ui (docs/ui/sistema-de-diseno.md) */
// «Tus datos»: the figures (from the case or typed), the spouse's figures, the constraints and the
// date of the plan. Everything typed is layered over the case until «Guardar en mi expediente».

import { ArrowRight, Loader2, PiggyBank, RotateCcw, Save, TriangleAlert } from "lucide-react";
import clsx from "clsx";

import { stated } from "../../lib/formulario";
import { COUNT_NAMES, ESTADOS, EXTRA, FAMILIA, type FieldDef, HECHO, INGRESOS, type Iliquidez, type LimitName, MONOPARENTAL, SPOUSE } from "../../lib/optimizador/campos";
import { COMUNIDADES } from "../../lib/presentation";
import { EditorPosiciones } from "./EditorPosiciones";
import { Collapsible, Field, Group, INPUT } from "./piezas";
import type { Optimizador } from "./useOptimizador";

interface Props {
  o: Optimizador;
  /** Whether saving to the case is offered (a case and a way to write its facts). */
  puedeGuardar: boolean;
  busy: boolean;
  onOpenSimulator: () => void;
}

export function OptimizadorDatos({ o, puedeGuardar, busy, onOpenSimulator }: Props) {
  const { vista, base, peticion, guardado, problemIds, stale, today, year, conExpediente, showCifras, state, acciones } = o;
  const { comunidad, estado, married, hijos, tributacionActual, iliquidez, value, spouseValue, limitValue } = vista;
  const { savedComunidad, savedEstado, caseChildren, storedSpouse, storedPrefs, spouseBaseline, tributacionBaseline, limitsBaseline, baseline } = base;
  const { problems, datos } = peticion;
  const { cambios } = guardado;
  const { form, computed, loading, error, saving, saveError } = state;
  const { fechaEdit } = form;
  const { calcular, setForm, setEdit } = acciones;
  const invalid = problemIds.length > 0;
  const fecha = fechaEdit ?? today;
  const fechaPasada = fecha > `${year}-12-31`;
  const limitDirty = (name: LimitName) => conExpediente && stated("money", limitValue(name)) !== stated("money", limitsBaseline[name]);
  const shown = new Set(form.extraFields);
  for (const f of EXTRA) if (f.name in baseline) shown.add(f.name);
  // With the per-child detail in the case, the children's disability comes from it (edited in the simulator).
  const visibleExtra = EXTRA.filter((f) => shown.has(f.name) && !(caseChildren !== null && COUNT_NAMES.has(f.name)));

const renderUserField = (def: FieldDef) => (
  <Field
    key={def.name}
    id={`opt-${def.name}`}
    def={def}
    value={value(def.name)}
    onChange={(raw) => setEdit(def.name, raw)}
    problem={problems[`opt-${def.name}`]}
    dirty={def.name in datos}
  />
);


  const addable = EXTRA.filter(
    (f) => !visibleExtra.includes(f) && !(caseChildren !== null && COUNT_NAMES.has(f.name)),
  );
  return (
    <form id="opt-form" onSubmit={(e) => void calcular(e)} noValidate className="space-y-5">
      <p className="text-[13px] leading-snug text-ink-soft">
        {conExpediente
          ? "Partimos de los datos de tu expediente. Puedes corregirlos aquí: los cambios solo cuentan para este cálculo hasta que los guardes."
          : "Escribe tus cifras del año. Una casilla vacía cuenta como «no lo sé», no como cero: el plan te dirá qué datos le faltan."}
      </p>

      <Collapsible
        id="opt-cifras"
        open={showCifras}
        onToggle={() => acciones.setCifrasOpen(!showCifras)}
        title={
          <>
            Tus cifras
            {conExpediente && <span className="ml-1.5 font-normal text-muted">(de tu expediente)</span>}
          </>
        }
      >
        <div className="space-y-5">
          <Group title="Ingresos del año">{INGRESOS.map(renderUserField)}</Group>
          <Group title="Lo que ya has hecho este año">{HECHO.map(renderUserField)}</Group>
          <Group title="Tu situación">
            <div>
              <label htmlFor="opt-comunidad_autonoma" className="block text-[12.5px] leading-snug text-ink-soft">
                Comunidad autónoma
              </label>
              <select
                id="opt-comunidad_autonoma"
                value={comunidad}
                onChange={(e) => setForm((f) => ({ ...f, comunidadEdit: e.target.value }))}
                className={clsx(INPUT, "mt-1", "comunidad_autonoma" in datos ? "border-primary" : "border-line focus:border-primary")}
              >
                {COMUNIDADES.filter((c) => c.value || !savedComunidad).map((c) => (
                  <option key={c.value} value={c.value}>
                    {c.label}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label htmlFor="opt-estado_civil" className="block text-[12.5px] leading-snug text-ink-soft">
                Estado civil
              </label>
              <select
                id="opt-estado_civil"
                value={estado}
                onChange={(e) => setForm((f) => ({ ...f, estadoEdit: e.target.value }))}
                aria-describedby="opt-estado_civil-hint"
                className={clsx(INPUT, "mt-1", "estado_civil" in datos ? "border-primary" : "border-line focus:border-primary")}
              >
                {ESTADOS.filter((e) => e.value || !savedEstado).map((e) => (
                  <option key={e.value} value={e.value}>
                    {e.label}
                  </option>
                ))}
              </select>
              <span id="opt-estado_civil-hint" className="mt-1 block text-[11.5px] leading-snug text-muted">
                Si estás casado o casada, se compara la declaración conjunta.
              </span>
            </div>
            {caseChildren !== null ? (
              <>
                {FAMILIA.slice(0, 1).map(renderUserField)}
                <p className="text-[12.5px] leading-snug text-ink-soft sm:col-span-2">
                  Hijos: {caseChildren}, del detalle de cada hijo de tu expediente.{" "}
                  <button type="button" onClick={onOpenSimulator} className="font-semibold text-primary-dark underline underline-offset-2 hover:no-underline">
                    Se edita en el simulador
                  </button>
                  .
                </p>
              </>
            ) : (
              FAMILIA.map(renderUserField)
            )}
            {!married && hijos > 0 && renderUserField(MONOPARENTAL)}
          </Group>
          <Group
            title="Más datos que pueden cambiar tus deducciones"
            note={
              <>
                Gastos de guardería, alquiler, ascendientes… Una medida puede bajar tu base por debajo del límite de una deducción: si tienes alguno de estos gastos, añádelo.
                {caseChildren === null && " Para el detalle de cada hijo, usa el simulador."}
              </>
            }
          >
            {visibleExtra.map(renderUserField)}
            {addable.length > 0 && (
              <div className="sm:col-span-2">
                <label htmlFor="opt-add-extra" className="block text-[12.5px] leading-snug text-ink-soft">
                  Añadir un dato
                </label>
                <select
                  id="opt-add-extra"
                  value=""
                  onChange={(e) => {
                    if (e.target.value) acciones.addExtra(e.target.value);
                  }}
                  className={clsx(INPUT, "mt-1 border-line focus:border-primary")}
                >
                  <option value="">Elige un dato…</option>
                  {addable.map((f) => (
                    <option key={f.name} value={f.name}>
                      {f.label}
                    </option>
                  ))}
                </select>
              </div>
            )}
          </Group>
        </div>
      </Collapsible>

      {married && (
        <div className="rounded-xl border border-line bg-surface px-4 py-3.5">
        <Group
          title="Tu cónyuge"
          note={
            "Para comparar la declaración conjunta con las individuales hacen falta sus cifras por separado. Sin ellas, el plan no puede valorar la conjunta." +
            (storedSpouse ? " Las que ya tienes guardadas en tu expediente aparecen rellenas." : "")
          }
        >
          {SPOUSE.map((def) => (
            <Field
              key={def.name}
              id={`opt-conyuge-${def.name}`}
              def={def}
              value={spouseValue(def.name)}
              onChange={(raw) => acciones.setSpouseEdit(def.name, raw)}
              problem={problems[`opt-conyuge-${def.name}`]}
              dirty={conExpediente && stated(def.kind, spouseValue(def.name)) !== stated(def.kind, spouseBaseline[def.name])}
            />
          ))}
          <div className="sm:col-span-2">
            <label htmlFor="opt-tributacion-actual" className="block text-[12.5px] leading-snug text-ink-soft">
              Cómo pensabais declarar
            </label>
            <select
              id="opt-tributacion-actual"
              value={tributacionActual}
              onChange={(e) => setForm((f) => ({ ...f, tributacionEdit: e.target.value === "conjunta_biparental" ? "conjunta_biparental" : "individual" }))}
              aria-describedby="opt-tributacion-actual-hint"
              className={clsx(INPUT, "mt-1", conExpediente && tributacionActual !== tributacionBaseline ? "border-primary" : "border-line focus:border-primary")}
            >
              <option value="individual">Cada uno su declaración (individual)</option>
              <option value="conjunta_biparental">Una declaración conjunta</option>
            </select>
            <span id="opt-tributacion-actual-hint" className="mt-1 block text-[11.5px] leading-snug text-muted">
              Es el punto de partida con el que se compara el plan; el plan valora las dos opciones.
            </span>
          </div>
        </Group>
        </div>
      )}

      <div className="border-t border-line pt-4">
        <h3 className="font-serif text-[16px] font-bold text-ink">Lo que puedes hacer antes de fin de año</h3>
        <p className="mt-0.5 text-[12.5px] leading-snug text-ink-soft">
          El plan solo propone lo que aceptes aquí, y nunca por encima de los importes que indiques.
          {storedPrefs && " Lo que guardaste en tu expediente aparece ya rellenado."}
        </p>
      </div>

      <Group title="Planes de pensiones">
        <Field
          id="opt-aportacion_maxima_pensiones"
          def={{
            name: "aportacion_maxima_pensiones",
            label: "Lo que aún puedes aportar a tu plan",
            kind: "money",
            hint: "Este año, además de lo ya aportado. Vacío si no lo sabes: el plan supone que puedes llegar al máximo que reduce tu impuesto, y te lo dice.",
          }}
          value={limitValue("aportacion_maxima_pensiones")}
          onChange={(raw) => acciones.setLimit("aportacion_maxima_pensiones", raw)}
          problem={problems["opt-aportacion_maxima_pensiones"]}
          dirty={limitDirty("aportacion_maxima_pensiones")}
        />
        {married && (
          <Field
            id="opt-aportacion_maxima_pensiones_conyuge"
            def={{ name: "aportacion_maxima_pensiones_conyuge", label: "Y al plan de tu cónyuge", kind: "money", hint: "Igual: vacío si no lo sabes." }}
            value={limitValue("aportacion_maxima_pensiones_conyuge")}
            onChange={(raw) => acciones.setLimit("aportacion_maxima_pensiones_conyuge", raw)}
            problem={problems["opt-aportacion_maxima_pensiones_conyuge"]}
            dirty={limitDirty("aportacion_maxima_pensiones_conyuge")}
          />
        )}
        <fieldset className="sm:col-span-2" aria-describedby="opt-iliquidez-hint">
          <legend className="block text-[12.5px] leading-snug text-ink-soft">¿Aceptas que el dinero quede inmovilizado hasta la jubilación?</legend>
          <p id="opt-iliquidez-hint" className="mt-0.5 text-[11.5px] leading-snug text-muted">
            Lo aportado a un plan de pensiones solo se puede rescatar en la jubilación y en otros supuestos tasados.
          </p>
          <div className="mt-1.5 space-y-1.5">
            {(
              [
                ["si", "Sí, acepto que el dinero quede inmovilizado hasta la jubilación"],
                ["no", "No: necesito poder disponer de ese dinero (no se proponen planes de pensiones)"],
                ["", "Aún no lo sé (el plan supondrá que sí y te lo dirá)"],
              ] as Array<[Iliquidez, string]>
            ).map(([v, label]) => (
              <label key={v || "nose"} className="flex items-start gap-2.5 text-[13.5px] text-ink">
                <input
                  type="radio"
                  name="opt-iliquidez"
                  id={v === "si" ? "opt-acepta_iliquidez" : undefined}
                  checked={iliquidez === v}
                  onChange={() => setForm((f) => ({ ...f, iliquidezEdit: v }))}
                  className="mt-0.5 h-4 w-4 shrink-0 accent-[rgb(var(--primary))]"
                />
                {label}
              </label>
            ))}
          </div>
        </fieldset>
      </Group>

      <Group title="Donativos">
        <Field
          id="opt-donativo_previsto"
          def={{
            name: "donativo_previsto",
            label: "Donativo que harás de todos modos",
            kind: "money",
            hint: "El plan elige a qué tipo de entidad conviene hacerlo (y, si sois matrimonio, quién lo hace); no te propone donar más.",
          }}
          value={limitValue("donativo_previsto")}
          onChange={(raw) => acciones.setLimit("donativo_previsto", raw)}
          problem={problems["opt-donativo_previsto"]}
          dirty={limitDirty("donativo_previsto")}
        />
        <Field
          id="opt-presupuesto_donativos"
          def={{
            name: "presupuesto_donativos",
            label: "Presupuesto máximo (opcional)",
            kind: "money",
            hint: "Si lo indicas, el plan puede proponerte donar algo más, nunca por encima de este importe. Donar siempre cuesta dinero: verás su coste neto.",
          }}
          value={limitValue("presupuesto_donativos")}
          onChange={(raw) => acciones.setLimit("presupuesto_donativos", raw)}
          problem={problems["opt-presupuesto_donativos"]}
          dirty={limitDirty("presupuesto_donativos")}
        />
        <div className="sm:col-span-2">
        <Field
          id="opt-donativo_recurrente_importe_anterior"
          def={{
            name: "donativo_recurrente_importe_anterior",
            label: "Lo que donaste el año pasado a la misma entidad (opcional)",
            kind: "money",
            hint: "Solo si le donas desde hace al menos dos años y el año pasado no le diste menos que el anterior: parte del donativo deduce más (art. 19.1 Ley 49/2002).",
          }}
          value={limitValue("donativo_recurrente_importe_anterior")}
          onChange={(raw) => acciones.setLimit("donativo_recurrente_importe_anterior", raw)}
          problem={problems["opt-donativo_recurrente_importe_anterior"]}
          dirty={limitDirty("donativo_recurrente_importe_anterior")}
        />
        </div>
      </Group>

      <EditorPosiciones o={o} />

      <fieldset className="min-w-0">
        <legend className="mb-2 text-[11px] font-semibold uppercase tracking-[0.12em] text-muted">Fecha del plan</legend>
        <div className="flex flex-wrap items-end gap-2">
          <div>
            <label htmlFor="opt-fecha" className="block text-[12.5px] leading-snug text-ink-soft">
              Día para el que se hace el plan
            </label>
            <input
              id="opt-fecha"
              type="date"
              value={fecha}
              onChange={(e) => setForm((f) => ({ ...f, fechaEdit: e.target.value }))}
              aria-describedby="opt-fecha-hint"
              aria-invalid={problems["opt-fecha"] ? true : undefined}
              className={clsx(INPUT, "mt-1 block w-auto", problems["opt-fecha"] ? "border-risk-high-fg" : fechaEdit && fechaEdit !== today ? "border-primary" : "border-line focus:border-primary")}
            />
          </div>
          {fechaEdit !== null && fechaEdit !== today && (
            <button type="button" onClick={() => setForm((f) => ({ ...f, fechaEdit: null }))} className="rounded-lg px-2.5 py-2 text-[12.5px] font-semibold text-primary-dark hover:bg-line/60">
              Volver a hoy
            </button>
          )}
        </div>
        <p id="opt-fecha-hint" className="mt-1 text-[11.5px] leading-snug text-muted">
          Por defecto, hoy. Hasta el 31 de diciembre de {year} aún puedes aportar, vender o donar; después solo quedan las opciones de la declaración (conjunta o individual).
        </p>
        {problems["opt-fecha"] && <p className="mt-1 text-[11.5px] font-semibold text-risk-high-fg">{problems["opt-fecha"]}</p>}
        {fechaPasada && (
          <p className="mt-1.5 rounded-lg bg-grounding-abstain-bg px-3 py-1.5 text-[12.5px] text-grounding-abstain-fg">
            Con esta fecha el ejercicio {year} ya ha terminado: solo se valoran las opciones de la declaración.
          </p>
        )}
      </fieldset>

      <div className="sticky bottom-0 z-10 -mx-4 border-t border-line bg-paper px-4 pb-1 pt-3 sm:-mx-5 sm:px-5">
        {error && (
          <p id="opt-error" role="alert" className="mb-2 flex gap-2 rounded-lg border border-risk-high-fg/30 bg-risk-high-bg px-3 py-2 text-[13px] text-ink-soft">
            <TriangleAlert size={15} aria-hidden className="mt-0.5 shrink-0 text-risk-high-fg" />
            <span>{error}</span>
          </p>
        )}
        {invalid ? (
          <p role="status" className="mb-2 flex gap-1.5 text-[12.5px] font-semibold text-risk-high-fg">
            <TriangleAlert size={15} aria-hidden className="mt-0.5 shrink-0" />
            Hay {problemIds.length === 1 ? "un dato" : `${problemIds.length} datos`} que revisar (en rojo).
          </p>
        ) : (
          stale && (
            <p role="status" className="mb-2 text-[12.5px] font-semibold text-grounding-uncited-fg">
              Has cambiado datos: el plan calculado ya no corresponde a lo que has escrito.
            </p>
          )
        )}
        {puedeGuardar && cambios > 0 && (
          <div className="mb-2 flex flex-wrap items-center gap-2">
            <button
              type="button"
              onClick={() => void acciones.saveToCase()}
              disabled={saving || busy}
              className="inline-flex items-center gap-1.5 rounded-lg border border-line bg-surface px-3 py-2 text-[13px] font-semibold text-primary-dark transition hover:border-primary disabled:cursor-not-allowed disabled:opacity-50"
            >
              <Save size={14} aria-hidden />
              {saving ? "Guardando…" : `Guardar ${cambios === 1 ? "este cambio" : `estos ${cambios} cambios`} en mi expediente`}
            </button>
            <button type="button" onClick={acciones.undoChanges} className="inline-flex items-center gap-1.5 rounded-lg px-3 py-2 text-[13px] font-semibold text-ink-soft hover:bg-line/60">
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
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="submit"
            disabled={loading}
            aria-describedby={error ? "opt-error" : undefined}
            className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-4 py-2.5 text-[14px] font-semibold text-white transition hover:bg-primary-hover disabled:cursor-wait disabled:opacity-70"
          >
            {loading ? <Loader2 size={16} aria-hidden className="animate-spin" /> : <PiggyBank size={16} aria-hidden />}
            {loading ? "Calculando…" : computed ? "Recalcular mi plan" : "Calcular mi plan"}
          </button>
          {computed && !stale && !loading && (
            <button type="button" onClick={() => acciones.showTab("plan")} className="inline-flex items-center gap-1 rounded-lg px-3 py-2.5 text-[13px] font-semibold text-primary-dark hover:bg-line/60">
              Ver mi plan
              <ArrowRight size={14} aria-hidden />
            </button>
          )}
        </div>
      </div>
    </form>
  );
}
