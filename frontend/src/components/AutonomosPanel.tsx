// Self-employed panel: annual net income of the activity, the quarterly IRPF
// payment (modelo 130) and the quarterly VAT return (modelo 303). Everything
// is the deterministic calculators behind POST /autonomos/* — no model call,
// so it recomputes as the user types. Same layout as the simulator: the form, and
// the result in a sidebar that follows it on wide screens.
//
// Shown as a page (registro.ts: presentacion "pagina"): the shell puts the title and the frame.

import { type ReactNode, useEffect, useMemo, useRef, useState } from "react";

import type { CalcLine, Fact } from "../api";
import { ApiError } from "../api";
import type { ActividadPerfil, ResultadoActividad, ResultadoModelo130, ResultadoModelo303, Trimestre } from "../apiAutonomos";
import { calcularActividad, calcularActividadDelExpediente, calcularModelo130, calcularModelo303, getEjerciciosAutonomos } from "../apiAutonomos";
import { formatMoney } from "../lib/presentation";
import { AvisoError, Button, CargandoBloque, Flash, Select, SegmentedControl } from "../ui";
import { ArrowRightIcon, HistoryIcon } from "../ui/iconos";
import { CampoCasilla, CampoCifra, CampoSelector, Plegable, Resultado, SeccionFormulario, TablaCifras } from "./calculos/piezas";
import { Pestanas } from "./calculos/Pestanas";

type Tab = "actividad" | "m130" | "m303";
const TABS: Array<{ id: Tab; label: string }> = [
  { id: "actividad", label: "Mi actividad" },
  { id: "m130", label: "Pago trimestral (130)" },
  { id: "m303", label: "IVA trimestral (303)" },
];
const TRIMESTRES: Record<Trimestre, { value: Trimestre; label: string; meses: string; hasta: string }> = {
  1: { value: 1, label: "1.º", meses: "enero a marzo", hasta: "31 de marzo" },
  2: { value: 2, label: "2.º", meses: "abril a junio", hasta: "30 de junio" },
  3: { value: 3, label: "3.º", meses: "julio a septiembre", hasta: "30 de septiembre" },
  4: { value: 4, label: "4.º", meses: "octubre a diciembre", hasta: "31 de diciembre" },
};

/** «1.º»…«4.º» for a quarter sent by the server (empty if it is out of range). */
function etiquetaTrimestre(n: number): string {
  return n === 1 || n === 2 || n === 3 || n === 4 ? TRIMESTRES[n].label : "";
}

interface FieldDef {
  name: string;
  label: string;
  hint?: string;
}

const ACTIVIDAD_INGRESOS: FieldDef[] = [
  { name: "ingresos_integros", label: "Ingresos del año", hint: "Todo lo facturado en el año, sin IVA y antes de retenciones" },
];
const ACTIVIDAD_GASTOS: FieldDef[] = [
  { name: "seguridad_social_autonomo", label: "Cuota de autónomos", hint: "Lo pagado a la Seguridad Social (RETA) en el año" },
  { name: "suministros", label: "Suministros", hint: "Luz, agua, gas, teléfono e internet de la actividad" },
  { name: "alquileres", label: "Alquiler del local u oficina" },
  { name: "servicios_profesionales", label: "Gestoría y otros profesionales" },
  { name: "amortizaciones", label: "Amortizaciones", hint: "La parte del año de equipos, vehículos, mobiliario…" },
  { name: "otros_gastos", label: "Otros gastos", hint: "Material, seguros, software, publicidad, viajes…" },
];
const ACTIVIDAD_IRREGULARES: FieldDef = {
  name: "rendimientos_irregulares",
  label: "Beneficio extraordinario de varios años",
  hint: "La parte del beneficio generada durante más de dos años o por una indemnización por cese, una subvención de capital o un premio. Tiene una reducción propia.",
};
// Only asked when there is no case to take them from.
const ACTIVIDAD_MAS: FieldDef[] = [
  { name: "otras_rentas_no_exentas", label: "Otros ingresos del año", hint: "Sueldo, alquileres, intereses… Déjalo vacío si no lo sabes: no se aplicarán las reducciones que dependen de ello" },
  { name: "reduccion_rendimientos_trabajo", label: "Reducción por rendimientos del trabajo", hint: "Solo si además tienes un sueldo bajo y conoces la reducción que se le aplica" },
  { name: "cifra_negocios_ano_anterior", label: "Facturación del año anterior", hint: "Para comprobar que puedes usar la modalidad simplificada" },
];
const M130_FIELDS: FieldDef[] = [
  { name: "ingresos_acumulados", label: "Ingresos acumulados", hint: "Sin IVA" },
  { name: "gastos_acumulados", label: "Gastos acumulados", hint: "Sin IVA; no añadas los gastos de difícil justificación, los suma el cálculo" },
  { name: "retenciones_acumuladas", label: "Retenciones acumuladas", hint: "El IRPF que tus clientes te han descontado en las facturas" },
];
const M303_VENTAS: FieldDef[] = [
  { name: "base_tipo_general", label: "Ventas al tipo general", hint: "Base (sin IVA) de lo facturado con el tipo general" },
  { name: "base_tipo_reducido", label: "Ventas al tipo reducido", hint: "Base (sin IVA)" },
  { name: "base_tipo_superreducido", label: "Ventas al tipo superreducido", hint: "Base (sin IVA)" },
];
const M303_COMPRAS: FieldDef[] = [
  { name: "cuotas_soportadas_deducibles", label: "IVA de tus gastos", hint: "La cuota de IVA (no la base) de las facturas de gastos corrientes" },
  { name: "cuotas_soportadas_bienes_inversion", label: "IVA de bienes de inversión", hint: "La cuota de IVA de equipos, vehículos, maquinaria…" },
  { name: "cuotas_a_compensar_periodos_anteriores", label: "Saldo a tu favor de trimestres anteriores", hint: "Lo que quedó «a compensar» en declaraciones anteriores" },
];

// Panel field -> the case fact that stores it (asistente/ambitos/fiscal/profile.py: ACTIVIDAD_FACT_FIELDS).
const FACT_OF: Record<string, string> = {
  ...Object.fromEntries([...ACTIVIDAD_INGRESOS, ...ACTIVIDAD_GASTOS].map((f) => [f.name, `actividad_${f.name}`])),
  rendimientos_irregulares: "actividad_rendimientos_irregulares",
};
const FLAG_FACT_OF: Record<string, string> = {
  cumple_requisitos_reduccion_dependiente: "actividad_cumple_requisitos_dependiente",
  reduccion_inicio_actividad: "actividad_reduccion_inicio",
};
const CEUTA_FACT = "actividad_ceuta_melilla";
const NET_FACT = "rendimiento_actividad_neto";

type Values = Record<string, string>;
type Flags = Record<string, boolean>;

function parseNumber(raw: string | undefined): number | null {
  const cleaned = (raw ?? "").trim().replace(/[€\s]/g, "").replace(/\.(?=\d{3}(\D|$))/g, "").replace(",", ".");
  if (!cleaned || cleaned === "-") return null;
  const n = Number(cleaned);
  return Number.isFinite(n) ? n : null;
}

/** Only the figures the user typed: an empty box is "not given", not zero. */
function numbers(values: Values, names: string[]): Record<string, number> {
  const out: Record<string, number> = {};
  for (const name of names) {
    const n = parseNumber(values[name]);
    if (n !== null) out[name] = n;
  }
  return out;
}

/** Debounced, last-request-wins call to one calculator. `bodyKey` is the JSON body; null = do not call. */
function useCalculo<T>(active: boolean, bodyKey: string | null, run: (body: unknown, signal: AbortSignal) => Promise<T>) {
  const [result, setResult] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  // The request («body|attempt») last answered, right or wrong: while it differs, a result is on its way.
  const [resueltoPara, setResueltoPara] = useState<string | null>(null);
  // Bumped by «Reintentar»: the same calculation is asked for again.
  const [intento, setIntento] = useState(0);
  const seq = useRef(0);
  const runRef = useRef(run);
  useEffect(() => {
    runRef.current = run;
  });
  useEffect(() => {
    if (!active || bodyKey === null) return;
    const mine = ++seq.current;
    const controller = new AbortController();
    const timer = setTimeout(() => {
      runRef.current(JSON.parse(bodyKey) as unknown, controller.signal).then(
        (value) => {
          if (mine !== seq.current) return;
          setResult(value);
          setError(null);
          setResueltoPara(`${bodyKey}|${intento}`);
        },
        (err: unknown) => {
          if (controller.signal.aborted || mine !== seq.current) return;
          setError(err instanceof ApiError ? err.message : "");
          setResueltoPara(`${bodyKey}|${intento}`);
        },
      );
    }, 300);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [active, bodyKey, intento]);
  const pendiente = active && bodyKey !== null && resueltoPara !== `${bodyKey}|${intento}`;
  return { result, error, pendiente, reintentar: () => setIntento((n) => n + 1) };
}

function MoneyField({ field, values, onChange }: { field: FieldDef; values: Values; onChange: (name: string, value: string) => void }) {
  return <CampoCifra etiqueta={field.label} ayuda={field.hint} valor={values[field.name] ?? ""} onCambio={(v) => onChange(field.name, v)} placeholder="0" />;
}

function Flag({ name, label, hint, flags, onChange }: { name: string; label: string; hint?: string; flags: Flags; onChange: (name: string, value: boolean) => void }) {
  return <CampoCasilla etiqueta={label} ayuda={hint} marcada={flags[name] ?? false} onCambio={(v) => onChange(name, v)} />;
}

function Optional({ title, children }: { title: string; children: ReactNode }) {
  return (
    <Plegable titulo={title}>
      <div className="grid gap-4 sm:grid-cols-2">{children}</div>
    </Plegable>
  );
}

function QuarterPicker({ value, onChange, cumulative }: { value: Trimestre; onChange: (value: Trimestre) => void; cumulative: boolean }) {
  const current = TRIMESTRES[value];
  const trimestres = Object.values(TRIMESTRES);
  return (
    <div className="flex flex-col gap-2">
      <p id="trimestre-titulo" className="m-0 text-sm font-semibold">
        Trimestre
      </p>
      <SegmentedControl aria-labelledby="trimestre-titulo" fullWidth onChange={(i) => onChange(trimestres[i]?.value ?? 1)}>
        {trimestres.map((t) => (
          <SegmentedControl.Button key={t.value} selected={t.value === value} aria-label={`${t.label} trimestre (${t.meses})`}>
            {t.label}
          </SegmentedControl.Button>
        ))}
      </SegmentedControl>
      <p className="m-0 text-xs text-fg-muted">
        {cumulative
          ? `Pon las cifras acumuladas desde el 1 de enero hasta el ${current.hasta}, no solo las del trimestre.`
          : `Pon solo las cifras del trimestre (${current.meses}).`}
      </p>
    </div>
  );
}

function Detail({ lineas, avisos }: { lineas: CalcLine[]; avisos: string[] }) {
  return (
    <>
      {lineas.length > 0 && (
        <Plegable titulo="Desglose del cálculo" sinRelleno>
          <TablaCifras titulo="Desglose del cálculo" filas={lineas.map((line) => ({ concepto: line.concepto, referencia: line.referencia, cifras: [formatMoney(line.importe)] }))} />
        </Plegable>
      )}
      {avisos.length > 0 && (
        <Plegable tono="aviso" abiertoAlInicio titulo={`Qué debes saber de este cálculo (${avisos.length})`}>
          <ul className="m-0 flex list-disc flex-col gap-1 pl-5">
            {avisos.map((aviso, i) => (
              <li key={i}>{aviso}</li>
            ))}
          </ul>
        </Plegable>
      )}
    </>
  );
}

const OPCIONES_CEUTA = [
  { value: "", label: "No" },
  { value: "ceuta", label: "Sí, en Ceuta" },
  { value: "melilla", label: "Sí, en Melilla" },
];

const OPCIONES_DISCAPACIDAD = [
  { value: "no", label: "No" },
  { value: "si", label: "Sí" },
  { value: "ampliada", label: "Sí, del 65 % o más, con movilidad reducida o ayuda de terceras personas" },
];

function CeutaSelect({ value, onChange }: { value: string; onChange: (value: string) => void }) {
  return (
    <CampoSelector
      etiqueta="¿Desarrollas la actividad en Ceuta o Melilla?"
      valor={value}
      onCambio={onChange}
      opciones={OPCIONES_CEUTA}
      ayuda="Solo si sus rendimientos tienen derecho a la deducción por rentas obtenidas en Ceuta o Melilla. Cambia los pagos trimestrales y, según el año, los gastos de difícil justificación."
      className="sm:col-span-2"
    />
  );
}

interface AutonomosPanelProps {
  /** The page is on screen: the calculators only run while it is. */
  open: boolean;
  /** "Usar en mi simulación": hands over the annual net income of the activity, in euros. */
  onUse: (net: number) => void;
  /** Fiscal year of the conversation ("2026"); used when the calculators have parameters for it. */
  taxPeriod?: string | null;
  /** With a case, its facts prefill "Mi actividad" and its other income decides the reductions. */
  caseId?: string | null;
  facts?: Fact[];
  /** Persist one fact to the case (null deletes it). Resolves true when saved. */
  onSaveFact?: (factName: string, value: string | number | boolean | null) => Promise<boolean>;
  busy?: boolean;
}

const NO_FACTS: Fact[] = [];

export function AutonomosPanel({ open, onUse, taxPeriod = null, caseId = null, facts = NO_FACTS, onSaveFact, busy = false }: AutonomosPanelProps) {
  const [tab, setTab] = useState<Tab>("actividad");
  const [ejercicios, setEjercicios] = useState<number[] | null>(null);
  const [ejercicioEdit, setEjercicioEdit] = useState<number | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  // Bumped by «Reintentar» when the years could not be loaded.
  const [intentoCarga, setIntentoCarga] = useState(0);
  // One set of typed figures per tab, kept while the panel stays mounted.
  const [actividad, setActividad] = useState<Values>({});
  const [actividadFlags, setActividadFlags] = useState<Flags>({});
  const [discapacidad, setDiscapacidad] = useState("no");
  // null = whatever the case says.
  const [ceutaEdit, setCeutaEdit] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [m130, setM130] = useState<Values>({});
  const [m130Flags, setM130Flags] = useState<Flags>({ aplicar_gastos_dificil_justificacion: true });
  const [trimestre130, setTrimestre130] = useState<Trimestre>(1);
  const [m303, setM303] = useState<Values>({});
  const [trimestre303, setTrimestre303] = useState<Trimestre>(1);

  useEffect(() => {
    if (!open || ejercicios !== null) return;
    const controller = new AbortController();
    getEjerciciosAutonomos(controller.signal).then(
      (years) => {
        setEjercicios(years);
        setLoadError(years.length ? null : "No hay ningún ejercicio disponible para estos cálculos.");
      },
      (err: unknown) => {
        if (controller.signal.aborted) return;
        setLoadError(err instanceof ApiError ? err.message : "");
      },
    );
    return () => controller.abort();
  }, [open, ejercicios, intentoCarga]);

  // What the case already holds about the activity: typed figures are edits over it.
  const saved = useMemo(() => {
    const byName = new Map(facts.map((fact) => [fact.name, fact.value]));
    const values: Values = {};
    for (const [field, fact] of Object.entries(FACT_OF)) {
      const value = byName.get(fact);
      if (typeof value === "number") values[field] = String(value);
    }
    const flags: Flags = {};
    for (const [field, fact] of Object.entries(FLAG_FACT_OF)) {
      const value = byName.get(fact);
      if (typeof value === "boolean") flags[field] = value;
    }
    const ciudad = byName.get(CEUTA_FACT);
    const profesional = byName.get("actividad_profesional");
    return {
      values,
      flags,
      ceuta: typeof ciudad === "string" && ["ceuta", "melilla"].includes(ciudad.trim().toLowerCase()) ? ciudad.trim().toLowerCase() : "",
      profesional: typeof profesional === "boolean" ? profesional : null,
      hasNet: byName.has(NET_FACT),
    };
  }, [facts]);
  const conExpediente = Boolean(caseId);
  const actividadValues: Values = { ...saved.values, ...actividad };
  const actividadFlagValues: Flags = { ...saved.flags, ...actividadFlags };
  const ceuta = ceutaEdit ?? saved.ceuta;

  const wanted = taxPeriod ? Number(taxPeriod.slice(0, 4)) : null;
  const ejercicio = ejercicios?.length ? (ejercicioEdit ?? (wanted !== null && ejercicios.includes(wanted) ? wanted : ejercicios[ejercicios.length - 1])) : null;

  // Without a case: the figures alone, with whatever "other income" the user types.
  const actividadBody =
    ejercicio === null || conExpediente
      ? null
      : JSON.stringify({
          ejercicio,
          ...numbers(actividadValues, [...Object.keys(FACT_OF), ...ACTIVIDAD_MAS.map((f) => f.name)]),
          ...(actividadFlagValues.cumple_requisitos_reduccion_dependiente ? { cumple_requisitos_reduccion_dependiente: true } : {}),
          ...(actividadFlagValues.reduccion_inicio_actividad ? { reduccion_inicio_actividad: true } : {}),
          ...(discapacidad === "si" ? { discapacidad: true } : discapacidad === "ampliada" ? { discapacidad_ampliada: true } : {}),
          ...(ceuta ? { ceuta_melilla: ceuta } : {}),
        });
  // With a case: the same figures as facts layered over the saved ones, so the case's other
  // income (salary, interest…) decides the reductions exactly as the simulator will.
  const cambiosExpediente: Record<string, unknown> = {};
  for (const [field, fact] of Object.entries(FACT_OF)) {
    const n = parseNumber(actividadValues[field]);
    if (n !== null) cambiosExpediente[fact] = n;
    else if (field in saved.values) cambiosExpediente[fact] = 0; // cleared in the form
  }
  for (const [field, fact] of Object.entries(FLAG_FACT_OF)) cambiosExpediente[fact] = actividadFlagValues[field] ?? false;
  if (ceuta || saved.ceuta) cambiosExpediente[CEUTA_FACT] = ceuta;
  const expedienteBody = ejercicio === null || !caseId ? null : JSON.stringify({ caseId, ejercicio, cambios: cambiosExpediente, facts: facts.length });

  // What "Guardar en mi expediente" would write: [fact, value | null to delete].
  const pendientes: Array<[string, string | number | boolean | null]> = [];
  for (const [field, fact] of Object.entries(FACT_OF)) {
    const n = parseNumber(actividadValues[field]);
    const before = field in saved.values ? Number(saved.values[field]) : null;
    if (n !== before) pendientes.push([fact, n]);
  }
  for (const [field, fact] of Object.entries(FLAG_FACT_OF)) {
    const value = actividadFlagValues[field] ?? false;
    if (value !== (saved.flags[field] ?? false)) pendientes.push([fact, value]);
  }
  if (ceuta !== saved.ceuta) pendientes.push([CEUTA_FACT, ceuta || null]);
  const primero130 = trimestre130 === 1;
  const profesional = m130Flags.actividad_profesional ?? saved.profesional ?? true;
  const inicio = m130Flags.inicio_actividad ?? false;
  const m130Body =
    ejercicio === null
      ? null
      : JSON.stringify({
          ejercicio,
          trimestre: trimestre130,
          ...numbers(m130, [
            ...M130_FIELDS.map((f) => f.name),
            "rendimiento_neto_ejercicio_anterior",
            // Nothing comes from "previous quarters" in the first one.
            ...(primero130 ? [] : ["pagos_trimestres_anteriores", "minoracion_pendiente_trimestres_anteriores"]),
            ...(profesional ? (inicio ? ["ingresos_con_retencion_acumulados"] : ["ingresos_ano_anterior", "ingresos_con_retencion_ano_anterior"]) : []),
          ]),
          actividad_profesional: profesional,
          aplicar_gastos_dificil_justificacion: m130Flags.aplicar_gastos_dificil_justificacion ?? true,
          ...(profesional && inicio ? { inicio_actividad: true } : {}),
          ...(ceuta ? { ceuta_melilla: ceuta } : {}),
        });
  const m303Body =
    ejercicio === null ? null : JSON.stringify({ ejercicio, trimestre: trimestre303, ...numbers(m303, [...M303_VENTAS, ...M303_COMPRAS].map((f) => f.name)) });

  const calcActividad = useCalculo<ResultadoActividad>(open && tab === "actividad", actividadBody, (body, signal) => calcularActividad(body as Parameters<typeof calcularActividad>[0], signal));
  const calcExpediente = useCalculo<ActividadPerfil | null>(open && tab === "actividad", expedienteBody, (body, signal) => {
    const { caseId: id, ejercicio: year, cambios } = body as { caseId: string; ejercicio: number; cambios: Record<string, unknown> };
    return calcularActividadDelExpediente({ caseId: id, ejercicio: year, cambios, signal });
  });
  const calc130 = useCalculo<ResultadoModelo130>(open && tab === "m130", m130Body, (body, signal) => calcularModelo130(body as Parameters<typeof calcularModelo130>[0], signal));
  const calc303 = useCalculo<ResultadoModelo303>(open && tab === "m303", m303Body, (body, signal) => calcularModelo303(body as Parameters<typeof calcularModelo303>[0], signal));

  const setIn = (setter: (update: (prev: Values) => Values) => void) => (name: string, value: string) => setter((prev) => ({ ...prev, [name]: value }));
  const setFlagIn = (setter: (update: (prev: Flags) => Flags) => void) => (name: string, value: boolean) => setter((prev) => ({ ...prev, [name]: value }));
  const fields = (defs: FieldDef[], values: Values, setter: (update: (prev: Values) => Values) => void) =>
    defs.map((field) => <MoneyField key={field.name} field={field} values={values} onChange={setIn(setter)} />);

  const perfil = conExpediente ? calcExpediente.result : null;
  const resultadoActividad = conExpediente ? (perfil?.resultado ?? null) : calcActividad.result;
  const current = tab === "actividad" ? (conExpediente ? calcExpediente : calcActividad) : tab === "m130" ? calc130 : calc303;
  const fuente = tab === "actividad" ? resultadoActividad?.fuente : (current.result as { fuente?: string } | null)?.fuente;
  const flagProps = { flags: { ...actividadFlagValues }, onChange: setFlagIn(setActividadFlags) };

  /** Write the typed figures to the case. The breakdown replaces a net figure saved before. */
  async function saveActividad(): Promise<boolean> {
    if (!onSaveFact) return false;
    setSaving(true);
    try {
      for (const [fact, value] of pendientes) {
        if (!(await onSaveFact(fact, value))) return false;
      }
      if (saved.hasNet && parseNumber(actividadValues.ingresos_integros) !== null && !(await onSaveFact(NET_FACT, null))) return false;
      setActividad({});
      setActividadFlags({});
      setCeutaEdit(null);
      return true;
    } finally {
      setSaving(false);
    }
  }


  function deshacerActividad() {
    setActividad({});
    setActividadFlags({});
    setCeutaEdit(null);
  }

  /** «Mi actividad»: the result (for the sidebar) and the form. */
  function actividadTab(): { resultado: ReactNode; formulario: ReactNode } {
    const r = resultadoActividad;
    const neto = r ? Number(r.rendimiento_actividad_neto) : 0;
    const hasIncome = parseNumber(actividadValues.ingresos_integros) !== null;
    const canSave = conExpediente && onSaveFact !== undefined;
    const reducciones = r ? Number(r.reduccion_art_32_1) + Number(r.reduccion_art_32_2) + Number(r.reduccion_art_32_3) : 0;
    const resultado = r ? (
      <Resultado titulo="Rendimiento neto de tu actividad" cifra={formatMoney(r.rendimiento_actividad_neto)} sufijo={neto < 0 ? "de pérdida" : "al año"}>
        <p className="m-0 text-sm">
          Ingresos {formatMoney(r.ingresos_integros)} · gastos {formatMoney(r.gastos_deducibles)}
          {Number(r.gastos_dificil_justificacion) > 0 && <> · gastos de difícil justificación {formatMoney(r.gastos_dificil_justificacion)}</>}
          {reducciones > 0 && <> · reducciones {formatMoney(reducciones)}</>}
        </p>
        <p className="m-0 text-xs text-fg-muted">Es la cifra por la que tu actividad tributa en el IRPF, antes de sumar tus otros ingresos.</p>
        {perfil?.otras_rentas_no_exentas != null && (
          <p className="m-0 text-xs text-fg-muted">
            Se han tenido en cuenta los otros ingresos de tu expediente ({formatMoney(perfil.otras_rentas_no_exentas)}) para ver qué reducciones te corresponden.
          </p>
        )}
        {hasIncome ? (
          <div className="flex flex-wrap gap-2">
            <Button
              variant="primary"
              trailingVisual={ArrowRightIcon}
              disabled={saving || busy}
              onClick={() => {
                // With a case the breakdown itself is saved, so the simulator recomputes it with the rest of the case.
                if (!canSave) onUse(neto);
                else void saveActividad().then((ok) => ok && onUse(neto));
              }}
            >
              Usar en mi simulación
            </Button>
            {canSave && pendientes.length > 0 && (
              <>
                <Button disabled={saving || busy} loading={saving} onClick={() => void saveActividad()}>
                  Guardar en mi expediente
                </Button>
                <Button variant="invisible" leadingVisual={HistoryIcon} onClick={deshacerActividad}>
                  Deshacer cambios
                </Button>
              </>
            )}
          </div>
        ) : (
          <p className="m-0 text-sm text-fg-muted">Introduce al menos tus ingresos del año para obtener el cálculo.</p>
        )}
        {canSave && pendientes.length > 0 && hasIncome && (
          <p className="m-0 text-xs text-fg-muted">Tienes cambios sin guardar: al guardarlos, el simulador y el asesor calcularán tu actividad con estas cifras.</p>
        )}
      </Resultado>
    ) : conExpediente && !current.error ? (
      <p className="m-0 text-sm text-fg-muted">Introduce tus ingresos del año para calcular el beneficio de tu actividad.</p>
    ) : null;
    const formulario = (
      <>
        <SeccionFormulario titulo="Ingresos">{fields(ACTIVIDAD_INGRESOS, actividadValues, setActividad)}</SeccionFormulario>
        <SeccionFormulario titulo="Gastos del año (sin IVA)">{fields(ACTIVIDAD_GASTOS, actividadValues, setActividad)}</SeccionFormulario>
        <Optional title="Más datos (opcional)">
          {!conExpediente && fields(ACTIVIDAD_MAS, actividadValues, setActividad)}
          {conExpediente && (
            <p className="m-0 text-xs text-fg-muted sm:col-span-2">
              Tus otros ingresos (sueldo, intereses, alquileres…) y tu grado de discapacidad se toman de tu expediente: cámbialos en «Tus datos» o en el simulador.
            </p>
          )}
          <MoneyField field={ACTIVIDAD_IRREGULARES} values={actividadValues} onChange={setIn(setActividad)} />
          <Flag
            name="cumple_requisitos_reduccion_dependiente"
            label="Trabajo para un único cliente y cumplo los requisitos de la reducción"
            hint="Autónomo dependiente o de cliente único no vinculado, sin sueldo y con retención en casi todos los ingresos. Sustituye a los gastos de difícil justificación; el resultado detalla los requisitos."
            {...flagProps}
          />
          <Flag
            name="reduccion_inicio_actividad"
            label="Es una actividad nueva y este es mi primer año con beneficio (o el siguiente)"
            hint="Solo si no ejerciste ninguna actividad el año anterior a empezar y la mayor parte de tus ingresos no viene de tu antiguo empleador. Tiene una reducción propia."
            {...flagProps}
          />
          <CeutaSelect value={ceuta} onChange={setCeutaEdit} />
          {!conExpediente && (
            <CampoSelector
              etiqueta="Discapacidad reconocida"
              valor={discapacidad}
              onCambio={setDiscapacidad}
              opciones={OPCIONES_DISCAPACIDAD}
              ayuda="Solo cuenta junto con la reducción de cliente único."
              className="sm:col-span-2"
            />
          )}
        </Optional>
        {r && <Detail lineas={r.lineas} avisos={perfil ? [perfil.aviso, ...r.avisos] : r.avisos} />}
        {!r && perfil && <Detail lineas={[]} avisos={[perfil.aviso]} />}
      </>
    );
    return { resultado, formulario };
  }

  /** «Pago trimestral (130)». */
  function m130Tab(): { resultado: ReactNode; formulario: ReactNode } {
    const r = calc130.result;
    const obligado = r?.obligado_a_pago_fraccionado;
    const resultado = r && (
      <Resultado
        titulo={`Pago del ${etiquetaTrimestre(r.trimestre)} trimestre (modelo 130)`}
        cifra={formatMoney(r.a_ingresar)}
        sufijo={Number(r.a_ingresar) > 0 ? "a ingresar" : "nada que ingresar"}
        favorable={Number(r.a_ingresar) === 0}
      >
        <p className="m-0 text-sm">
          Beneficio acumulado {formatMoney(r.rendimiento_neto_acumulado)} · pago a cuenta sobre ese beneficio {formatMoney(r.pago_sobre_rendimiento)}
        </p>
        <Flash variant={obligado === false ? "success" : obligado === null ? "default" : "warning"}>
          <strong>{obligado === false ? "No tienes que hacer este pago. " : obligado === null ? "No sabemos si tienes que presentarlo. " : "Tienes que presentarlo. "}</strong>
          {r.motivo_obligacion}
          {obligado === false && Number(r.resultado) > 0 && <> Si tuvieras que presentarlo, saldrían {formatMoney(r.resultado)}.</>}
        </Flash>
        {r.trimestre < 4 && (
          <p className="m-0 text-xs text-fg-muted">
            Para el trimestre siguiente, apunta: pago antes de la minoración {formatMoney(r.pago_previo_a_minoracion)}
            {Number(r.minoracion_pendiente) > 0 && <> y minoración pendiente {formatMoney(r.minoracion_pendiente)}</>}.
          </p>
        )}
      </Resultado>
    );
    const formulario = (
      <>
        <QuarterPicker value={trimestre130} onChange={setTrimestre130} cumulative />
        <SeccionFormulario titulo="Desde el 1 de enero">
          {fields(M130_FIELDS, m130, setM130)}
          {!primero130 && (
            <MoneyField
              field={{ name: "pagos_trimestres_anteriores", label: "Pagos de los trimestres anteriores", hint: "Suma de los «pagos antes de la minoración» de los trimestres anteriores de este año" }}
              values={m130}
              onChange={setIn(setM130)}
            />
          )}
          <Flag
            name="actividad_profesional"
            label="Mi actividad es profesional"
            hint="Abogados, médicos, programadores, diseñadores… (no comercio, hostelería o talleres). Solo los profesionales restan las retenciones."
            flags={{ ...m130Flags, actividad_profesional: profesional }}
            onChange={setFlagIn(setM130Flags)}
          />
          <Flag
            name="aplicar_gastos_dificil_justificacion"
            label="Estoy en estimación directa simplificada"
            hint="Lo habitual. El cálculo añade el porcentaje de gastos de difícil justificación que permite la norma."
            flags={m130Flags}
            onChange={setFlagIn(setM130Flags)}
          />
        </SeccionFormulario>
        <Optional title="Rebaja por ingresos bajos el año pasado (opcional)">
          <MoneyField
            field={{ name: "rendimiento_neto_ejercicio_anterior", label: "Beneficio neto de la actividad el año pasado", hint: "Si fue bajo, el pago se rebaja una cantidad fija cada trimestre; el resultado te dirá cuánto" }}
            values={m130}
            onChange={setIn(setM130)}
          />
          {!primero130 && (
            <MoneyField
              field={{ name: "minoracion_pendiente_trimestres_anteriores", label: "Rebaja sin usar de trimestres anteriores", hint: "La «minoración pendiente» que te quedó en el trimestre anterior" }}
              values={m130}
              onChange={setIn(setM130)}
            />
          )}
        </Optional>
        <Optional title="Ceuta y Melilla (opcional)">
          <CeutaSelect value={ceuta} onChange={setCeutaEdit} />
        </Optional>
        {profesional && (
          <Optional title="¿Tengo que presentarlo? (opcional)">
            <Flag name="inicio_actividad" label="He empezado la actividad este año" flags={m130Flags} onChange={setFlagIn(setM130Flags)} />
            {inicio ? (
              <MoneyField
                field={{ name: "ingresos_con_retencion_acumulados", label: "Ingresos de este año con retención", hint: "De los ingresos acumulados, los facturados con retención de IRPF" }}
                values={m130}
                onChange={setIn(setM130)}
              />
            ) : (
              <>
                <MoneyField field={{ name: "ingresos_ano_anterior", label: "Ingresos del año pasado" }} values={m130} onChange={setIn(setM130)} />
                <MoneyField
                  field={{ name: "ingresos_con_retencion_ano_anterior", label: "De ellos, con retención", hint: "Si la mayor parte llevaba retención de IRPF, no tienes que presentar el modelo 130; el resultado te lo dirá" }}
                  values={m130}
                  onChange={setIn(setM130)}
                />
              </>
            )}
          </Optional>
        )}
        {r && <Detail lineas={r.lineas} avisos={r.avisos} />}
      </>
    );
    return { resultado, formulario };
  }

  /** «IVA trimestral (303)». */
  function m303Tab(): { resultado: ReactNode; formulario: ReactNode } {
    const r = calc303.result;
    const resultado303 = r ? Number(r.resultado) : 0;
    const resultado = r && (
      <Resultado
        titulo={`IVA del ${etiquetaTrimestre(r.trimestre)} trimestre (modelo 303)`}
        cifra={formatMoney(Math.abs(resultado303))}
        sufijo={r.tipo_resultado === "a_ingresar" ? "a ingresar" : r.tipo_resultado === "a_compensar" ? "a tu favor" : "sin nada que pagar"}
        favorable={r.tipo_resultado !== "a_ingresar"}
      >
        <p className="m-0 text-sm">
          IVA cobrado {formatMoney(r.iva_devengado)} · IVA pagado {formatMoney(r.iva_deducible)}
          {Number(r.compensacion_aplicada) > 0 && <> · compensado de antes {formatMoney(r.compensacion_aplicada)}</>}
        </p>
        {r.tipo_resultado === "a_compensar" && (
          <Flash variant="success">
            {r.puede_solicitar_devolucion
              ? "Es el último trimestre del año: puedes pedir la devolución o dejarlo a compensar."
              : "Se resta en tus próximas declaraciones; la devolución solo se puede pedir en la del cuarto trimestre."}
          </Flash>
        )}
      </Resultado>
    );
    const formulario = (
      <>
        <QuarterPicker value={trimestre303} onChange={setTrimestre303} cumulative={false} />
        <SeccionFormulario titulo="Lo que has facturado en el trimestre">{fields(M303_VENTAS, m303, setM303)}</SeccionFormulario>
        <SeccionFormulario titulo="Lo que puedes restar">{fields(M303_COMPRAS, m303, setM303)}</SeccionFormulario>
        {r && <Detail lineas={r.lineas} avisos={r.avisos} />}
      </>
    );
    return { resultado, formulario };
  }

  const vista = tab === "actividad" ? actividadTab() : tab === "m130" ? m130Tab() : m303Tab();

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-end justify-between gap-2">
        <Pestanas etiqueta="Cálculos para autónomos" idBase="autonomos" actual={tab} onCambiar={setTab} pestanas={TABS.map((t) => ({ id: t.id, etiqueta: t.label }))} />
        {ejercicios && ejercicios.length > 1 && ejercicio !== null && (
          <Select aria-label="Ejercicio" size="small" value={String(ejercicio)} onChange={(e) => setEjercicioEdit(Number(e.target.value))}>
            {ejercicios.map((year) => (
              <Select.Option key={year} value={String(year)}>
                {year}
              </Select.Option>
            ))}
          </Select>
        )}
      </div>

      <div role="tabpanel" id="autonomos-tabpanel" aria-labelledby={`autonomos-tab-${tab}`} className="grid items-start gap-6 lg:grid-cols-[minmax(0,1fr)_20rem]">
        <aside aria-label="Resultado" className="flex min-w-0 flex-col gap-4 lg:sticky lg:top-4 lg:order-2">
          {loadError !== null && (
            <AvisoError
              frase="No he podido preparar los cálculos de autónomos."
              detalle={loadError}
              onReintentar={() => {
                setLoadError(null);
                setEjercicios(null);
                setIntentoCarga((n) => n + 1);
              }}
            />
          )}
          {current.error !== null && <AvisoError frase="No he podido hacer el cálculo." detalle={current.error} onReintentar={current.reintentar} />}
          {/* Until the first result: its place, at its height, so the form does not jump when it arrives. While
              the years load (first), that place says so instead of the result's hint. */}
          {ejercicios === null && loadError === null ? (
            <CargandoBloque alto="13.25rem" etiqueta="Preparando los cálculos…" />
          ) : (
            <>
              {vista.resultado}
              {!vista.resultado && current.pendiente && current.error === null && <CargandoBloque alto="13.25rem" etiqueta="Calculando…" />}
            </>
          )}
        </aside>
        {/* Keyed by tab so React does not reuse one tab's DOM (open <details>, focus) for another. */}
        <div key={tab} className="flex min-w-0 flex-col gap-6">
          {vista.formulario}
          <p className="m-0 text-xs text-fg-muted">
            Calculado con fórmulas fijas{ejercicio ? ` (ejercicio ${ejercicio})` : ""} a partir de las cifras que indicas, sin inteligencia artificial; no valora si un gasto es deducible ni sustituye a la Agencia Tributaria.
            {fuente && <span className="block">{fuente}.</span>}
          </p>
        </div>
      </div>
    </div>
  );
}
