// The boxes of «Optimizar mis impuestos» and what the case already says about each of them.
// Pure: the screen (components/OptimizerPanel.tsx) and the request (./peticion.ts) read these.

import type { Fact } from "../../api";
import type { TipoActivo, Titular } from "../../apiOptimizador";
import {
  DATOS_CONYUGE,
  PREFERENCIAS_OPTIMIZACION,
  type StoredPosition,
  type StoredPreferences,
  parseStoredObject,
  storedNumber,
  storedPositions,
} from "../expediente";
import { type FieldDef as BaseFieldDef, amountText, parseAmount } from "../formulario";
import { comunidadDeExpediente, factLabel } from "../presentation";
import { TIPO_ACTIVO } from "./textos";

export type Kind = "money" | "signed" | "count" | "age" | "percent" | "flag";
export type FieldDef = BaseFieldDef<Kind>;

export const INGRESOS: FieldDef[] = [
  { name: "rendimiento_trabajo_bruto", label: "Salario bruto anual", kind: "money" },
  { name: "cotizaciones_seguridad_social", label: "Cotizaciones a la Seguridad Social", kind: "money", hint: "Lo que te descuentan en nómina por Seguridad Social" },
  { name: "retenciones", label: "IRPF ya retenido", kind: "money", hint: "Lo retenido en nómina en el año: decide si te sale a pagar o a devolver" },
  { name: "rendimiento_actividad_neto", label: "Beneficio neto de tu actividad", kind: "signed", hint: "Si eres autónomo (con signo menos si hay pérdidas)" },
  { name: "rendimiento_capital_mobiliario", label: "Intereses y dividendos", kind: "signed" },
  { name: "ganancias_patrimoniales", label: "Ganancias por ventas ya hechas este año", kind: "signed", hint: "Fondos, acciones, inmuebles… ya vendidos. Con signo menos si perdiste" },
];
export const HECHO: FieldDef[] = [
  { name: "aportacion_plan_pensiones", label: "Ya aportado a tu plan este año", kind: "money", hint: "Planes de pensiones y otros sistemas de previsión social" },
  { name: "donativos", label: "Ya donado este año", kind: "money", hint: "A entidades acogidas a la Ley 49/2002" },
  { name: "perdidas_patrimoniales_pendientes", label: "Pérdidas de años anteriores sin compensar", kind: "money", hint: "Las que te quedaron pendientes en declaraciones anteriores" },
];
export const FAMILIA: FieldDef[] = [
  { name: "edad", label: "Tu edad", kind: "age" },
  { name: "num_descendientes", label: "Hijos a cargo", kind: "count" },
  { name: "num_descendientes_menores_3", label: "De ellos, menores de 3 años", kind: "count" },
];
export const MONOPARENTAL: FieldDef = {
  name: "familia_monoparental",
  label: "Familia monoparental: vivo solo o sola con mis hijos",
  kind: "flag",
  hint: "Así se compara también la declaración conjunta con tus hijos.",
};
// Other figures that can move a deduction (offered under «Más datos»; the simulator has them all).
export const EXTRA: FieldDef[] = [
  { name: "gastos_guarderia", label: factLabel("gastos_guarderia"), kind: "money" },
  { name: "num_hijos_primer_ciclo_infantil", label: factLabel("num_hijos_primer_ciclo_infantil"), kind: "count" },
  { name: "hijos_nacidos_en_el_ejercicio", label: factLabel("hijos_nacidos_en_el_ejercicio"), kind: "count" },
  { name: "hijos_nacidos_ultimos_3_ejercicios", label: factLabel("hijos_nacidos_ultimos_3_ejercicios"), kind: "count" },
  { name: "gastos_escolaridad", label: factLabel("gastos_escolaridad"), kind: "money" },
  { name: "gastos_libros_texto", label: factLabel("gastos_libros_texto"), kind: "money" },
  { name: "gastos_ensenanza_idiomas", label: factLabel("gastos_ensenanza_idiomas"), kind: "money" },
  { name: "cotizaciones_empleada_hogar", label: factLabel("cotizaciones_empleada_hogar"), kind: "money" },
  { name: "gastos_cuidado_familiares", label: factLabel("gastos_cuidado_familiares"), kind: "money" },
  { name: "alquiler_vivienda_habitual", label: factLabel("alquiler_vivienda_habitual"), kind: "money" },
  { name: "grado_discapacidad", label: factLabel("grado_discapacidad"), kind: "percent" },
  { name: "necesita_asistencia", label: "Necesito ayuda de terceras personas o tengo movilidad reducida", kind: "flag", hint: "Con una discapacidad del 33 % al 64 %, suma los gastos de asistencia al mínimo" },
  { name: "num_descendientes_discapacidad", label: "Hijos con discapacidad del 33 % o más", kind: "count", hint: "Cuentan a cualquier edad si viven contigo" },
  { name: "num_descendientes_discapacidad_65", label: "De ellos, con discapacidad del 65 % o más", kind: "count" },
  { name: "num_descendientes_asistencia", label: "Hijos del 33 % al 64 % que necesitan ayuda de terceros", kind: "count", hint: "O que tienen movilidad reducida (desde el 65 % ya se suma)" },
  { name: "num_ascendientes_mayores_65", label: "Padres o abuelos mayores de 65 que viven contigo", kind: "count", hint: "Al menos medio año y sin rentas de más de 8.000 €. Los de tu cónyuge, en su declaración." },
  { name: "num_ascendientes_mayores_75", label: "De ellos, mayores de 75", kind: "count" },
  { name: "num_ascendientes_discapacidad", label: "Padres o abuelos con discapacidad del 33 % o más", kind: "count", hint: "Que viven contigo, tengan la edad que tengan" },
  { name: "num_ascendientes_discapacidad_hasta_65", label: "De ellos, de 65 años o menos", kind: "count", hint: "También dan derecho al mínimo por ascendientes" },
  { name: "num_ascendientes_discapacidad_65", label: "De ellos, con discapacidad del 65 % o más", kind: "count" },
  { name: "num_ascendientes_asistencia", label: "Padres o abuelos del 33 % al 64 % que necesitan ayuda de terceros", kind: "count", hint: "O que tienen movilidad reducida" },
  { name: "num_declaraciones_mismos_ascendientes", label: "Declaraciones que aplican el mínimo por ellos", kind: "count", hint: "Contando la tuya: 2 si un hermano también vive con ellos; se reparte" },
  { name: "rendimiento_alquiler_neto", label: factLabel("rendimiento_alquiler_neto"), kind: "signed" },
  { name: "rendimientos_negativos_pendientes", label: factLabel("rendimientos_negativos_pendientes"), kind: "money" },
  { name: "contribucion_empresa_plan_pensiones", label: factLabel("contribucion_empresa_plan_pensiones"), kind: "money" },
  { name: "familia_numerosa", label: factLabel("familia_numerosa"), kind: "flag" },
];
export const SPOUSE: FieldDef[] = [
  { name: "rendimiento_trabajo_bruto", label: "Su salario bruto anual", kind: "money" },
  { name: "cotizaciones_seguridad_social", label: "Sus cotizaciones a la Seguridad Social", kind: "money" },
  { name: "retenciones", label: "Su IRPF ya retenido", kind: "money" },
  { name: "rendimiento_actividad_neto", label: "Beneficio neto de su actividad", kind: "signed" },
  { name: "rendimiento_capital_mobiliario", label: "Sus intereses y dividendos", kind: "signed" },
  { name: "ganancias_patrimoniales", label: "Sus ganancias por ventas de este año", kind: "signed" },
  { name: "aportacion_plan_pensiones", label: "Ya aportado a su plan este año", kind: "money" },
  { name: "edad", label: "Su edad", kind: "age" },
  { name: "grado_discapacidad", label: "Su grado de discapacidad", kind: "percent" },
  { name: "necesita_asistencia", label: "Necesita ayuda de terceras personas o tiene movilidad reducida", kind: "flag", hint: "Con una discapacidad del 33 % al 64 %, suma los gastos de asistencia a su mínimo" },
];
export const USER_FIELDS = [...INGRESOS, ...HECHO, ...FAMILIA, MONOPARENTAL, ...EXTRA];
export const FIELD_BY_NAME = new Map(USER_FIELDS.map((f) => [f.name, f]));
export const EXTRA_NAMES = new Set(EXTRA.map((f) => f.name));
// Counts the per-child detail of the case already gives: never typed over it (the calculator would refuse them).
export const COUNT_NAMES = new Set([
  "num_descendientes",
  "num_descendientes_menores_3",
  "num_descendientes_discapacidad",
  "num_descendientes_discapacidad_65",
  "num_descendientes_asistencia",
]);

export const ESTADOS = [
  { value: "", label: "Sin indicar" },
  { value: "soltero", label: "Soltero o soltera" },
  { value: "casado", label: "Casado o casada" },
  { value: "pareja_de_hecho", label: "Pareja de hecho" },
  { value: "separado", label: "Separado o separada" },
  { value: "divorciado", label: "Divorciado o divorciada" },
  { value: "viudo", label: "Viudo o viuda" },
];

export function estadoDeExpediente(value: unknown): string {
  if (typeof value !== "string") return "";
  const saved = value.trim().toLowerCase();
  return ESTADOS.find((e) => e.value && saved.startsWith(e.value.slice(0, 4)))?.value ?? "";
}

/** Children in the case's per-child detail (the simulator edits it), or null when there is none. */
export function childrenInCase(facts: Fact[]): number | null {
  const fact = facts.find((f) => f.name === "descendientes");
  if (!fact) return null;
  try {
    const list: unknown = typeof fact.value === "string" ? JSON.parse(fact.value) : fact.value;
    return Array.isArray(list) ? list.length : null;
  } catch {
    return null;
  }
}

export interface Position {
  id: string;
  clase: "perdida" | "ganancia";
  descripcion: string;
  importe: string;
  tipo: TipoActivo;
  titular: Titular;
}

export type Iliquidez = "" | "si" | "no";
export type TributacionPrevista = "individual" | "conjunta_biparental";

// The money constraints of the form (fields of `preferencias_optimizacion` too).
export const LIMIT_NAMES = [
  "aportacion_maxima_pensiones",
  "aportacion_maxima_pensiones_conyuge",
  "donativo_previsto",
  "presupuesto_donativos",
  "donativo_recurrente_importe_anterior",
] as const;
export type LimitName = (typeof LIMIT_NAMES)[number];
// The case's own facts the plan does NOT depend on through `perfil`: the screen sends their content in full.
export const PLAN_INPUT_FACTS = new Set([DATOS_CONYUGE, PREFERENCIAS_OPTIMIZACION]);
const TIPOS = new Set<string>(TIPO_ACTIVO.map((t) => t.value));

/** A stored latent position as a row of the form (rows stored without an id get a stable one). */
export function storedRow(p: StoredPosition, clase: Position["clase"], index: number): Position {
  return {
    id: p.id ?? `guardada-${clase}-${index + 1}`,
    clase,
    descripcion: p.descripcion ?? "",
    importe: amountText(storedNumber(p.importe) ?? 0),
    tipo: p.tipo && TIPOS.has(p.tipo) ? (p.tipo as TipoActivo) : "valores_cotizados",
    titular: p.titular === "conyuge" ? "conyuge" : "contribuyente",
  };
}

/** An empty row of the latent positions. */
export function nuevaPosicion(clase: Position["clase"], id: string): Position {
  return { id, clase, descripcion: "", importe: "", tipo: "valores_cotizados", titular: "contribuyente" };
}

/** The rows as what would be stored (same order, trimmed description, parsed amount). */
export function rowsKey(rows: Position[]): string {
  return JSON.stringify(rows.map((p) => [p.id, p.clase, p.descripcion.trim(), parseAmount(p.importe), p.tipo, p.titular]));
}

/** Everything the case says that the screen starts from, as the boxes show it. */
export interface BaseExpediente {
  /** The user's own figures and flags: { fact: text of the box }. */
  baseline: Record<string, string>;
  savedComunidad: string;
  savedEstado: string;
  caseChildren: number | null;
  storedSpouse: Record<string, unknown> | null;
  storedPrefs: StoredPreferences | null;
  spouseBaseline: Record<string, string>;
  limitsBaseline: Partial<Record<LimitName, string>>;
  positionsBaseline: Position[];
  iliquidezBaseline: Iliquidez;
  tributacionBaseline: TributacionPrevista;
}

export function baseDeExpediente(facts: Fact[]): BaseExpediente {
  const baseline: Record<string, string> = {};
  for (const fact of facts) {
    if (typeof fact.value === "number") baseline[fact.name] = amountText(fact.value);
    else if (typeof fact.value === "boolean") baseline[fact.name] = String(fact.value);
  }
  const storedSpouse = parseStoredObject(facts.find((f) => f.name === DATOS_CONYUGE)?.value);
  const storedPrefs = parseStoredObject(facts.find((f) => f.name === PREFERENCIAS_OPTIMIZACION)?.value) as StoredPreferences | null;
  const spouseBaseline: Record<string, string> = {};
  for (const def of SPOUSE) {
    const v = storedSpouse?.[def.name];
    const n = storedNumber(v);
    if (typeof v === "boolean") spouseBaseline[def.name] = String(v);
    else if (n !== null) spouseBaseline[def.name] = amountText(n);
  }
  const limitsBaseline: Partial<Record<LimitName, string>> = {};
  for (const name of LIMIT_NAMES) {
    const n = storedNumber(storedPrefs?.[name]);
    if (n !== null) limitsBaseline[name] = amountText(n);
  }
  return {
    baseline,
    savedComunidad: comunidadDeExpediente(facts.find((f) => f.name === "comunidad_autonoma")?.value),
    savedEstado: estadoDeExpediente(facts.find((f) => f.name === "estado_civil")?.value),
    caseChildren: childrenInCase(facts),
    storedSpouse,
    storedPrefs,
    spouseBaseline,
    limitsBaseline,
    positionsBaseline: [
      ...storedPositions(storedPrefs?.perdidas_latentes).map((p, i) => storedRow(p, "perdida", i)),
      ...storedPositions(storedPrefs?.ganancias_latentes).map((p, i) => storedRow(p, "ganancia", i)),
    ],
    iliquidezBaseline: storedPrefs?.acepta_iliquidez === true ? "si" : storedPrefs?.acepta_iliquidez === false ? "no" : "",
    tributacionBaseline: storedPrefs?.tributacion_prevista === "conjunta_biparental" ? "conjunta_biparental" : "individual",
  };
}
