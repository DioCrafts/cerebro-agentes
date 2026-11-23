// A council session loaded by id and polled while the council works. A failed load is retried with
// a growing wait (4 s, 8 s, 16 s… up to a minute); a request the server refuses (4xx) is not.
// Changing the case or the session drops the previous one at once: a stale session is never shown
// under another id.

import { useEffect, useState } from "react";

import { ApiError } from "../api";
import { EN_MARCHA, type SesionConsejo, obtenerSesion } from "../apiConsejo";
import { errorMessage } from "../lib/memoria";

const INTERVALO_MS = 4000;
const ESPERA_MAXIMA_MS = 60_000;
const REINTENTOS = 6;

/** How long to wait before retry number `intento` (0-based) of a failed load. */
export function esperaReintento(intento: number): number {
  return Math.min(INTERVALO_MS * 2 ** intento, ESPERA_MAXIMA_MS);
}

/** Whether a failed load is worth retrying: network trouble and server errors, not a refusal. */
export function reintentable(err: unknown): boolean {
  if (!(err instanceof ApiError) || err.status === null) return true;
  return err.status >= 500 || err.status === 408 || err.status === 429;
}

interface Cargado {
  clave: string;
  sesion: SesionConsejo | null;
  error: string | null;
}

export function useSesionConsejo(caseId: string | null, sesionId: string | null): { sesion: SesionConsejo | null; error: string | null; recargar: () => void } {
  const clave = caseId && sesionId ? `${caseId}/${sesionId}` : null;
  const [cargado, setCargado] = useState<Cargado | null>(null);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    if (!caseId || !sesionId) return;
    const actual = `${caseId}/${sesionId}`;
    const controller = new AbortController();
    let temporizador: ReturnType<typeof setTimeout> | undefined;
    let fallos = 0;
    const cargar = () => {
      obtenerSesion(caseId, sesionId, controller.signal).then(
        (s) => {
          if (controller.signal.aborted) return;
          fallos = 0;
          setCargado({ clave: actual, sesion: s, error: null });
          if (EN_MARCHA.has(s.estado)) temporizador = setTimeout(cargar, INTERVALO_MS);
        },
        (err: unknown) => {
          if (controller.signal.aborted) return;
          // Keep the last session read for this id; only the error changes.
          setCargado((prev) => ({ clave: actual, sesion: prev?.clave === actual ? prev.sesion : null, error: errorMessage(err) }));
          if (reintentable(err) && fallos < REINTENTOS) temporizador = setTimeout(cargar, esperaReintento(fallos++));
        },
      );
    };
    cargar();
    return () => {
      controller.abort();
      if (temporizador) clearTimeout(temporizador);
    };
  }, [caseId, sesionId, tick]);

  const vigente = cargado !== null && cargado.clave === clave ? cargado : null;
  return { sesion: vigente?.sesion ?? null, error: vigente?.error ?? null, recargar: () => setTick((n) => n + 1) };
}
