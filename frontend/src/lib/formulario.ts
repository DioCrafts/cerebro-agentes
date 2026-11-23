// What the calculator forms (simulator, optimiser) share: the definition of a box, how a typed
// figure is read («1.500,50», «1500.5», «1.500 €») and how it is shown back. No React here.

/** What a box holds: an amount (≥ 0 or with sign), a whole count, an age, a percentage, a year or a yes/no. */
export type FieldKind = "money" | "signed" | "count" | "age" | "percent" | "year" | "flag";

export interface FieldDef<K extends FieldKind = FieldKind> {
  name: string;
  label: string;
  kind: K;
  hint?: string;
  /** Narrower amounts this field is the total of (the calculator keeps total ≥ sum of parts, and takes an empty total as that sum). */
  parts?: FieldDef<K>[];
  /** Like `parts`, for a count whose parts are asked elsewhere in the form (named, not nested). */
  sumOf?: string[];
}

/** "1.500,50", "1500.5", "1.500 €" → 1500.5; empty or unreadable → null. */
export function parseAmount(raw: string | undefined): number | null {
  const cleaned = (raw ?? "").trim().replace(/[€\s]/g, "").replace(/\.(?=\d{3}(\D|$))/g, "").replace(",", ".");
  if (!cleaned || cleaned === "-") return null;
  const n = Number(cleaned);
  return Number.isFinite(n) ? n : null;
}

/** 1500.5 → "1500,5": how the forms' boxes show a figure. */
export function amountText(n: number): string {
  return String(Math.round(n * 100) / 100).replace(".", ",");
}

/** What is wrong with a typed figure, or null. */
export function fieldProblem(kind: FieldKind, raw: string | undefined): string | null {
  if (kind === "flag" || !raw || !raw.trim()) return null;
  const n = parseAmount(raw);
  if (n === null) return "Escribe una cifra, por ejemplo 1.500,50.";
  if (kind === "money" && n < 0) return "No puede ser negativo.";
  if (kind === "count" && (!Number.isInteger(n) || n < 0 || n > 20)) return "Un número entero de 0 a 20.";
  if (kind === "age" && (!Number.isInteger(n) || n < 0 || n > 130)) return "Una edad en años, de 0 a 130.";
  if (kind === "percent" && (!Number.isInteger(n) || n < 0 || n > 100)) return "Un porcentaje entero de 0 a 100.";
  if (kind === "year" && (!Number.isInteger(n) || n < 1900 || n > 2100)) return "Un año de cuatro cifras.";
  return null;
}

/** A typed box as what it states ("" = nothing), so «1.500» and «1500,00» are the same figure. */
export function stated(kind: FieldKind, raw: string | undefined): string {
  if (kind === "flag") return raw === "true" ? "true" : "";
  const n = parseAmount(raw);
  return n === null ? "" : String(n);
}
