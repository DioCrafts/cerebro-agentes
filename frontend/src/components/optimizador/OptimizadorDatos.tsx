// «Tus datos»: the figures (from the case or typed), the spouse's figures, the constraints and the
// date of the plan. Everything typed is layered over the case until «Guardar en mi expediente».

import { stated } from "../../lib/formulario";
import { COUNT_NAMES, ESTADOS, EXTRA, FAMILIA, type FieldDef, HECHO, INGRESOS, type Iliquidez, type LimitName, MONOPARENTAL, SPOUSE } from "../../lib/optimizador/campos";
import { COMUNIDADES } from "../../lib/presentation";
import { Button, Caja, Flash, FormControl, Link, Radio, RadioGroup, TextInput } from "../../ui";
import { ArrowRightIcon, GraphIcon, HistoryIcon } from "../../ui/iconos";
import { CampoSelector, Plegable, SeccionFormulario } from "../calculos/piezas";
import { EditorPosiciones } from "./EditorPosiciones";
import { Field } from "./piezas";
import type { Optimizador } from "./useOptimizador";

interface Props {
  o: Optimizador;
  /** Whether saving to the case is offered (a case and a way to write its facts). */
  puedeGuardar: boolean;
  busy: boolean;
  onOpenSimulator: () => void;
}

const TRIBUTACION_ACTUAL = [
  { value: "individual", label: "Cada uno su declaración (individual)" },
  { value: "conjunta_biparental", label: "Una declaración conjunta" },
];

const ILIQUIDEZ: Array<[Iliquidez, string]> = [
  ["si", "Sí, acepto que el dinero quede inmovilizado hasta la jubilación"],
  ["no", "No: necesito poder disponer de ese dinero (no se proponen planes de pensiones)"],
  ["", "Aún no lo sé (el plan supondrá que sí y te lo dirá)"],
];

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
  const limitField = (name: LimitName, label: string, hint: string, className?: string) => (
    <Field
      id={`opt-${name}`}
      def={{ name, label, kind: "money", hint }}
      value={limitValue(name)}
      onChange={(raw) => acciones.setLimit(name, raw)}
      problem={problems[`opt-${name}`]}
      dirty={limitDirty(name)}
      className={className}
    />
  );

  const addable = EXTRA.filter((f) => !visibleExtra.includes(f) && !(caseChildren !== null && COUNT_NAMES.has(f.name)));
  return (
    <form id="opt-form" onSubmit={(e) => void calcular(e)} noValidate className="flex flex-col gap-6">
      <p className="m-0 text-sm text-fg-muted">
        {conExpediente
          ? "Partimos de los datos de tu expediente. Puedes corregirlos aquí: los cambios solo cuentan para este cálculo hasta que los guardes."
          : "Escribe tus cifras del año. Una casilla vacía cuenta como «no lo sé», no como cero: el plan te dirá qué datos le faltan."}
      </p>

      <Plegable
        id="opt-cifras"
        abierto={showCifras}
        onAlternar={() => acciones.setCifrasOpen(!showCifras)}
        titulo={
          <>
            Tus cifras
            {conExpediente && <span className="ml-1 font-normal text-fg-muted">(de tu expediente)</span>}
          </>
        }
      >
        <div className="flex flex-col gap-6 py-1">
          <SeccionFormulario titulo="Ingresos del año">{INGRESOS.map(renderUserField)}</SeccionFormulario>
          <SeccionFormulario titulo="Lo que ya has hecho este año">{HECHO.map(renderUserField)}</SeccionFormulario>
          <SeccionFormulario titulo="Tu situación">
            <CampoSelector
              id="opt-comunidad_autonoma"
              etiqueta="Comunidad autónoma"
              valor={comunidad}
              onCambio={(v) => setForm((f) => ({ ...f, comunidadEdit: v }))}
              opciones={COMUNIDADES.filter((c) => c.value || !savedComunidad)}
              cambiado={"comunidad_autonoma" in datos}
            />
            <CampoSelector
              id="opt-estado_civil"
              etiqueta="Estado civil"
              valor={estado}
              onCambio={(v) => setForm((f) => ({ ...f, estadoEdit: v }))}
              opciones={ESTADOS.filter((e) => e.value || !savedEstado)}
              ayuda="Si estás casado o casada, se compara la declaración conjunta."
              cambiado={"estado_civil" in datos}
            />
            {caseChildren !== null ? (
              <>
                {FAMILIA.slice(0, 1).map(renderUserField)}
                <p className="m-0 text-sm sm:col-span-2">
                  Hijos: {caseChildren}, del detalle de cada hijo de tu expediente.{" "}
                  <Link as="button" type="button" inline onClick={onOpenSimulator}>
                    Se edita en el simulador
                  </Link>
                  .
                </p>
              </>
            ) : (
              FAMILIA.map(renderUserField)
            )}
            {!married && hijos > 0 && renderUserField(MONOPARENTAL)}
          </SeccionFormulario>
          <SeccionFormulario
            titulo="Más datos que pueden cambiar tus deducciones"
            nota={
              <>
                Gastos de guardería, alquiler, ascendientes… Una medida puede bajar tu base por debajo del límite de una deducción: si tienes alguno de estos gastos, añádelo.
                {caseChildren === null && " Para el detalle de cada hijo, usa el simulador."}
              </>
            }
          >
            {visibleExtra.map(renderUserField)}
            {addable.length > 0 && (
              <CampoSelector
                id="opt-add-extra"
                etiqueta="Añadir un dato"
                valor=""
                onCambio={(v) => {
                  if (v) acciones.addExtra(v);
                }}
                opciones={[{ value: "", label: "Elige un dato…" }, ...addable.map((f) => ({ value: f.name, label: f.label }))]}
                className="sm:col-span-2"
              />
            )}
          </SeccionFormulario>
        </div>
      </Plegable>

      {married && (
        <Caja>
          <Caja.Cuerpo>
            <SeccionFormulario
              titulo="Tu cónyuge"
              nota={
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
              <CampoSelector
                id="opt-tributacion-actual"
                etiqueta="Cómo pensabais declarar"
                valor={tributacionActual}
                onCambio={(v) => setForm((f) => ({ ...f, tributacionEdit: v === "conjunta_biparental" ? "conjunta_biparental" : "individual" }))}
                opciones={TRIBUTACION_ACTUAL}
                ayuda="Es el punto de partida con el que se compara el plan; el plan valora las dos opciones."
                cambiado={conExpediente && tributacionActual !== tributacionBaseline}
                className="sm:col-span-2"
              />
            </SeccionFormulario>
          </Caja.Cuerpo>
        </Caja>
      )}

      <div>
        <h3 className="m-0 text-xl font-semibold">Lo que puedes hacer antes de fin de año</h3>
        <p className="m-0 mt-1 text-sm text-fg-muted">
          El plan solo propone lo que aceptes aquí, y nunca por encima de los importes que indiques.
          {storedPrefs && " Lo que guardaste en tu expediente aparece ya rellenado."}
        </p>
      </div>

      <SeccionFormulario titulo="Planes de pensiones">
        {limitField(
          "aportacion_maxima_pensiones",
          "Lo que aún puedes aportar a tu plan",
          "Este año, además de lo ya aportado. Vacío si no lo sabes: el plan supone que puedes llegar al máximo que reduce tu impuesto, y te lo dice.",
        )}
        {married && limitField("aportacion_maxima_pensiones_conyuge", "Y al plan de tu cónyuge", "Igual: vacío si no lo sabes.")}
        <div className="sm:col-span-2">
          <RadioGroup name="opt-iliquidez">
            <RadioGroup.Label>¿Aceptas que el dinero quede inmovilizado hasta la jubilación?</RadioGroup.Label>
            <RadioGroup.Caption>Lo aportado a un plan de pensiones solo se puede rescatar en la jubilación y en otros supuestos tasados.</RadioGroup.Caption>
            {ILIQUIDEZ.map(([v, label]) => (
              <FormControl key={v || "nose"} id={v === "si" ? "opt-acepta_iliquidez" : `opt-iliquidez-${v || "nose"}`}>
                <Radio value={v} checked={iliquidez === v} onChange={() => setForm((f) => ({ ...f, iliquidezEdit: v }))} />
                <FormControl.Label className="font-normal">{label}</FormControl.Label>
              </FormControl>
            ))}
          </RadioGroup>
        </div>
      </SeccionFormulario>

      <SeccionFormulario titulo="Donativos">
        {limitField(
          "donativo_previsto",
          "Donativo que harás de todos modos",
          "El plan elige a qué tipo de entidad conviene hacerlo (y, si sois matrimonio, quién lo hace); no te propone donar más.",
        )}
        {limitField(
          "presupuesto_donativos",
          "Presupuesto máximo (opcional)",
          "Si lo indicas, el plan puede proponerte donar algo más, nunca por encima de este importe. Donar siempre cuesta dinero: verás su coste neto.",
        )}
        {limitField(
          "donativo_recurrente_importe_anterior",
          "Lo que donaste el año pasado a la misma entidad (opcional)",
          "Solo si le donas desde hace al menos dos años y el año pasado no le diste menos que el anterior: parte del donativo deduce más (art. 19.1 Ley 49/2002).",
          "sm:col-span-2",
        )}
      </SeccionFormulario>

      <EditorPosiciones o={o} />

      <SeccionFormulario titulo="Fecha del plan">
        <div className="flex flex-wrap items-end gap-2 sm:col-span-2">
          <FormControl id="opt-fecha">
            <FormControl.Label>Día para el que se hace el plan</FormControl.Label>
            <TextInput
              type="date"
              value={fecha}
              onChange={(e) => setForm((f) => ({ ...f, fechaEdit: e.target.value }))}
              validationStatus={problems["opt-fecha"] ? "error" : undefined}
              className={!problems["opt-fecha"] && fechaEdit && fechaEdit !== today ? "!border-border-accent" : undefined}
            />
            <FormControl.Caption>
              Por defecto, hoy. Hasta el 31 de diciembre de {year} aún puedes aportar, vender o donar; después solo quedan las opciones de la declaración (conjunta o individual).
            </FormControl.Caption>
            {problems["opt-fecha"] && <FormControl.Validation variant="error">{problems["opt-fecha"]}</FormControl.Validation>}
          </FormControl>
          {fechaEdit !== null && fechaEdit !== today && (
            <Button variant="invisible" onClick={() => setForm((f) => ({ ...f, fechaEdit: null }))}>
              Volver a hoy
            </Button>
          )}
        </div>
        {fechaPasada && (
          <Flash className="sm:col-span-2">Con esta fecha el ejercicio {year} ya ha terminado: solo se valoran las opciones de la declaración.</Flash>
        )}
      </SeccionFormulario>

      <div className="sticky bottom-0 z-10 flex flex-col gap-2 border-t border-border-default bg-canvas-default py-3">
        {error && (
          <Flash id="opt-error" variant="danger" role="alert">
            {error}
          </Flash>
        )}
        {invalid ? (
          <p role="status" className="m-0 text-sm font-semibold text-fg-danger">
            Hay {problemIds.length === 1 ? "un dato" : `${problemIds.length} datos`} que revisar (en rojo).
          </p>
        ) : (
          stale && (
            <p role="status" className="m-0 text-sm font-semibold text-fg-attention">
              Has cambiado datos: el plan calculado ya no corresponde a lo que has escrito.
            </p>
          )
        )}
        {puedeGuardar && cambios > 0 && (
          <div className="flex flex-col gap-2">
            <div className="flex flex-wrap items-center gap-2">
              <Button onClick={() => void acciones.saveToCase()} disabled={saving || busy} loading={saving}>
                {`Guardar ${cambios === 1 ? "este cambio" : `estos ${cambios} cambios`} en mi expediente`}
              </Button>
              <Button variant="invisible" leadingVisual={HistoryIcon} onClick={acciones.undoChanges}>
                Deshacer cambios
              </Button>
            </div>
            {saveError && (
              <Flash variant="danger" role="alert">
                {saveError}
              </Flash>
            )}
          </div>
        )}
        <div className="flex flex-wrap items-center gap-2">
          <Button type="submit" variant="primary" leadingVisual={GraphIcon} loading={loading} aria-describedby={error ? "opt-error" : undefined}>
            {computed ? "Recalcular mi plan" : "Calcular mi plan"}
          </Button>
          {computed && !stale && !loading && (
            <Button variant="invisible" trailingVisual={ArrowRightIcon} onClick={() => acciones.showTab("plan")}>
              Ver mi plan
            </Button>
          )}
        </div>
      </div>
    </form>
  );
}
