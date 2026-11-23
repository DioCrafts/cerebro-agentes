// The detail of each child in the simulator: rows of the editor, what is wrong with one, how a row
// is sent to the API and the counts a complete detail determines. Pure.

import type { Fact } from "../../api";
import { parseAmount } from "../formulario";
import { factLabel } from "../presentation";

// Expenses the editor offers per child (the calculator accepts any expense input per child;
// other ones already in a saved detail are kept and shown too).
export const CHILD_EXPENSES: Array<{ name: string; label: string }> = [
  { name: "gastos_guarderia", label: "Guardería" },
  { name: "gastos_escolaridad", label: "Escolaridad" },
  { name: "gastos_libros_texto", label: "Libros y material" },
  { name: "gastos_ensenanza_idiomas", label: "Idiomas" },
  { name: "gastos_uniformes_escolares", label: "Uniformes" },
];

export interface ChildRow {
  id: string;
  fecha: string; // AAAA-MM-DD, or ""
  edad: string; // age at 31 December; ignored when the date is given
  discapacidad: string;
  asistencia: boolean; // needs third parties' help or has reduced mobility (counts from 33 % to 64 %)
  etapa: string;
  adopcion: string; // year of the adoption, or ""
  internacional: boolean;
  multiple: boolean;
  mesesGuarderia: string;
  gastos: Record<string, string>;
}
export type ApiChild = Record<string, unknown>;

export const emptyChild = (id: string): ChildRow => ({ id, fecha: "", edad: "", discapacidad: "", asistencia: false, etapa: "", adopcion: "", internacional: false, multiple: false, mesesGuarderia: "", gastos: {} });

/** Whether the degree of a row admits the assistance flag (33 % to 64 %; from 65 % it always counts). */
export function admitsAssistance(row: ChildRow): boolean {
  const grade = parseAmount(row.discapacidad);
  return grade !== null && grade >= 33 && grade < 65;
}

const text = (value: unknown): string => (typeof value === "number" || typeof value === "string" ? String(value) : "");

/** The saved `descendientes` fact (JSON text of a list) as editor rows. */
export function parseChildrenFact(facts: Fact[], newId: () => string): { rows: ChildRow[]; broken: boolean; saved: boolean } {
  const fact = facts.find((f) => f.name === "descendientes");
  if (!fact) return { rows: [], broken: false, saved: false };
  let list: unknown = fact.value;
  if (typeof list === "string") {
    try {
      list = JSON.parse(list);
    } catch {
      return { rows: [], broken: true, saved: true };
    }
  }
  if (!Array.isArray(list) || list.some((item) => typeof item !== "object" || item === null || Array.isArray(item))) return { rows: [], broken: true, saved: true };
  const rows = (list as Array<Record<string, unknown>>).map((item): ChildRow => {
    const gastos: Record<string, string> = {};
    if (typeof item.gastos === "object" && item.gastos !== null) {
      for (const [name, amount] of Object.entries(item.gastos as Record<string, unknown>)) gastos[name] = text(amount).replace(".", ",");
    }
    return {
      id: newId(),
      fecha: typeof item.fecha_nacimiento === "string" ? item.fecha_nacimiento : "",
      edad: typeof item.fecha_nacimiento === "string" ? "" : text(item.edad),
      discapacidad: text(item.grado_discapacidad),
      asistencia: item.necesita_asistencia === true,
      etapa: typeof item.etapa_educativa === "string" ? item.etapa_educativa : "",
      adopcion: text(item.ejercicio_adopcion),
      internacional: item.adopcion_internacional === true,
      multiple: item.parto_multiple === true,
      mesesGuarderia: text(item.meses_guarderia),
      gastos,
    };
  });
  return { rows, broken: false, saved: true };
}

/** Age reached at 31 December of `year`, or null while the row does not say. */
export function childAge(row: ChildRow, year: number): number | null {
  if (/^\d{4}-\d{2}-\d{2}$/.test(row.fecha)) return year - Number(row.fecha.slice(0, 4));
  const edad = parseAmount(row.edad);
  return edad !== null && Number.isInteger(edad) ? edad : null;
}

/** What is wrong with a row, in words; null when it can be sent. */
export function childProblem(row: ChildRow, year: number): string | null {
  const age = childAge(row, year);
  if (age === null) return "indica su fecha de nacimiento o la edad que cumple en el año.";
  if (age < 0) return `nació después de ${year}, el ejercicio que estás simulando.`;
  if (age > 130) return "revisa la fecha de nacimiento o la edad.";
  const whole = (raw: string, max: number) => {
    const n = parseAmount(raw);
    return raw.trim() === "" || (n !== null && Number.isInteger(n) && n >= 0 && n <= max);
  };
  if (!whole(row.discapacidad, 100)) return "el grado de discapacidad va de 0 a 100.";
  if (!whole(row.mesesGuarderia, 12)) return "los meses de guardería van de 0 a 12.";
  if (row.adopcion.trim() !== "") {
    const adopcion = parseAmount(row.adopcion);
    if (adopcion === null || !Number.isInteger(adopcion) || adopcion < year - age || adopcion > year) return `el año de adopción debe estar entre ${year - age} y ${year}.`;
  }
  for (const [name, raw] of Object.entries(row.gastos)) {
    const n = parseAmount(raw);
    if (raw.trim() !== "" && (n === null || n < 0)) return `revisa el importe de «${factLabel(name).toLowerCase()}».`;
  }
  return null;
}

/** A row as the API takes it (defaults left out, so equal details compare equal). */
export function childToApi(row: ChildRow): ApiChild {
  const out: ApiChild = {};
  if (/^\d{4}-\d{2}-\d{2}$/.test(row.fecha)) out.fecha_nacimiento = row.fecha;
  else out.edad = parseAmount(row.edad);
  const discapacidad = parseAmount(row.discapacidad);
  if (discapacidad) out.grado_discapacidad = discapacidad;
  if (row.asistencia && admitsAssistance(row)) out.necesita_asistencia = true;
  if (row.etapa) out.etapa_educativa = row.etapa;
  const adopcion = parseAmount(row.adopcion);
  if (adopcion !== null) {
    out.ejercicio_adopcion = adopcion;
    if (row.internacional) out.adopcion_internacional = true;
  }
  if (row.multiple) out.parto_multiple = true;
  const meses = parseAmount(row.mesesGuarderia);
  if (meses !== null) out.meses_guarderia = meses;
  const gastos: Record<string, number> = {};
  for (const name of Object.keys(row.gastos).sort()) {
    const n = parseAmount(row.gastos[name]);
    if (n) gastos[name] = n;
  }
  if (Object.keys(gastos).length > 0) out.gastos = gastos;
  return out;
}

/** The counts and per-child expense totals a complete detail determines. */
export function derivedFromChildren(rows: ChildRow[], year: number): Record<string, string> {
  const out: Record<string, string> = {};
  if (rows.length === 0 || rows.some((row) => childProblem(row, year) !== null)) return out;
  const arrival = (row: ChildRow) => year - (parseAmount(row.adopcion) ?? year - (childAge(row, year) ?? 0));
  const count = (test: (row: ChildRow) => boolean) => String(rows.filter(test).length);
  out.num_descendientes = String(rows.length);
  out.num_descendientes_menores_3 = count((row) => (childAge(row, year) ?? 0) < 3);
  out.hijos_nacidos_en_el_ejercicio = count((row) => arrival(row) === 0);
  out.hijos_nacidos_ultimos_3_ejercicios = count((row) => arrival(row) <= 2);
  out.hijos_adoptados_en_el_ejercicio = count((row) => parseAmount(row.adopcion) === year);
  out.num_hijos_primer_ciclo_infantil = count((row) => row.etapa === "infantil_primer_ciclo");
  const grade = (row: ChildRow) => parseAmount(row.discapacidad) ?? 0;
  out.num_descendientes_discapacidad = count((row) => grade(row) >= 33);
  out.num_descendientes_discapacidad_65 = count((row) => grade(row) >= 65);
  out.num_descendientes_asistencia = count((row) => row.asistencia && admitsAssistance(row));
  const totals: Record<string, number> = {};
  for (const row of rows) {
    for (const [name, raw] of Object.entries(row.gastos)) {
      const n = parseAmount(raw);
      if (n) totals[name] = (totals[name] ?? 0) + n;
    }
  }
  for (const [name, total] of Object.entries(totals)) out[name] = String(Math.round(total * 100) / 100).replace(".", ",");
  return out;
}
