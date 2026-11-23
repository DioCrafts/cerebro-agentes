// Type guards for what the API sends back (JSON of unknown shape). One place, so every client
// validates the same way.

/** A plain JSON object (not null, not an array). */
export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** A stored value as text: objects and lists as JSON (never «[object Object]»), the rest as `String`. */
export function textoDeValor(value: unknown): string {
  if (typeof value === "object" && value !== null) return JSON.stringify(value);
  return String(value);
}
