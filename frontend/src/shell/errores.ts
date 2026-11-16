// The user-facing wording of a failed request (shared by the shell's hooks).

import { ApiError } from "../api";

export function errorMessage(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  if (err instanceof TypeError) return "No hay conexión con el servicio. Comprueba que está en marcha y tu conexión a internet.";
  return "Ha ocurrido un error inesperado al preparar la respuesta.";
}
