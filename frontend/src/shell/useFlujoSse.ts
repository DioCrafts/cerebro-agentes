// Follow one thing the server keeps (a council session, a browser task) over its Server-Sent Events
// stream instead of polling: each event named `nombre` replaces the data, `fin` ends the follow-up.
//
// The stream is opened when `ruta` is set and closed when it changes or the component unmounts. A
// dropped connection is reopened with `Last-Event-ID` (the server does not resend what was seen),
// waiting longer after each consecutive failure (4 s, 8 s, 16 s… up to a minute); a refusal (4xx)
// is not retried. Changing `ruta` drops the previous data at once: stale data is never shown under
// another id.

import { useEffect, useState } from "react";

import { ApiError, abrirFlujo } from "../api";
import { seguirFlujo } from "../lib/sse";

/** Whether a failed attempt is worth another: network trouble and server errors, not a refusal. */
export function reintentable(err: unknown): boolean {
  if (!(err instanceof ApiError) || err.status === null) return true;
  return err.status >= 500 || err.status === 408 || err.status === 429;
}

function mensajeDeError(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  return "Se perdió la conexión con el servicio. Reintentando…";
}

export interface EstadoFlujo<T> {
  datos: T | null;
  error: string | null;
  /** The stream is open (or about to reopen); false once the server said `fin` or gave up. */
  enVivo: boolean;
}

interface Guardado<T> extends EstadoFlujo<T> {
  clave: string;
}

export function useFlujoSse<T>(
  ruta: string | null,
  nombre: string,
  validar: (value: unknown) => value is T,
  reinicio = 0,
): EstadoFlujo<T> {
  const [guardado, setGuardado] = useState<Guardado<T> | null>(null);

  useEffect(() => {
    if (ruta === null) return;
    const clave = ruta;
    return seguirFlujo({
      abrir: (ultimoId, signal) => abrirFlujo(clave, ultimoId, signal),
      alEvento: (evento) => {
        if (evento.event === "fin") {
          setGuardado((prev) => ({ clave, datos: prev?.clave === clave ? prev.datos : null, error: null, enVivo: false }));
          return "fin";
        }
        if (evento.event === nombre) {
          const datos: unknown = JSON.parse(evento.data);
          if (validar(datos)) setGuardado({ clave, datos, error: null, enVivo: true });
        }
        return "seguir";
      },
      alError: (err, reintentara) => {
        // Keep the last data read for this id; only the error changes.
        setGuardado((prev) => ({
          clave,
          datos: prev?.clave === clave ? prev.datos : null,
          error: mensajeDeError(err),
          enVivo: reintentara,
        }));
      },
      reintentable,
    });
    // `validar` is a module-level guard: it never changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ruta, nombre, reinicio]);

  const vigente = guardado !== null && guardado.clave === ruta ? guardado : null;
  return { datos: vigente?.datos ?? null, error: vigente?.error ?? null, enVivo: vigente?.enVivo ?? false };
}
