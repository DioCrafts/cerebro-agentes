// Small helpers of the decision-memory UI (MemoriaBotones, PreferenciasPanel).

import { ApiError } from "../api";

export function errorMessage(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  if (err instanceof TypeError) return "No hay conexión con el servicio. Comprueba que está en marcha y tu conexión a internet.";
  return "Ha ocurrido un error inesperado.";
}

/** Today plus `dias`, as a local calendar date "YYYY-MM-DD" (never shifted by the UTC offset). */
export function isoEnDias(dias: number): string {
  const d = new Date();
  d.setDate(d.getDate() + dias);
  const mm = String(d.getMonth() + 1).padStart(2, "0");
  const dd = String(d.getDate()).padStart(2, "0");
  return `${d.getFullYear()}-${mm}-${dd}`;
}
