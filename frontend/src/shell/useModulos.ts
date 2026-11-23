// Household modules: what exists and is on for this user, and the actions the specialists prepared that
// wait for the user's confirmation.

import { useCallback, useEffect, useMemo, useState } from "react";

import type { CaseState } from "../api";
import { ApiError, getCase } from "../api";
import type { AccionPendiente, ModulosResponse } from "../apiModulos";
import { confirmarAccion, getAcciones, getModulos, rechazarAccion, setModulo } from "../apiModulos";
import { errorMessage } from "./errores";

export interface Modulos {
  datos: ModulosResponse | null;
  error: string | null;
  nombres: Record<string, string>;
  /** The facts that belong to a module (kept out of «Tu expediente»). */
  hechos: Set<string>;
  cambiar: (clave: string, activo: boolean, borrar: boolean) => Promise<boolean>;
  /** Re-read the modules and the actions (when «Módulos» opens: a change made elsewhere shows up). */
  recargar: () => void;
  acciones: AccionPendiente[];
  pendientes: number;
  decidir: (id: string, confirmar: boolean) => Promise<void>;
  recargarAcciones: () => void;
}

export function useModulos(
  caseId: string | null,
  { aplicarCaso, alCambiar }: { aplicarCaso: (c: CaseState) => void; alCambiar: () => void },
): Modulos {
  const [datos, setDatos] = useState<ModulosResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  const [acciones, setAcciones] = useState<{ caseId: string; items: AccionPendiente[] } | null>(null);
  const [accionesTick, setAccionesTick] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    getModulos(controller.signal).then(
      (resp) => {
        setDatos(resp);
        setError(null);
      },
      (err: unknown) => {
        if (!controller.signal.aborted) setError(`No se pudieron cargar los módulos. ${errorMessage(err)}`);
      },
    );
    return () => controller.abort();
  }, [tick]);

  useEffect(() => {
    if (!caseId) return;
    const controller = new AbortController();
    getAcciones(caseId, controller.signal).then(
      (items) => setAcciones({ caseId, items }),
      (err: unknown) => {
        if (controller.signal.aborted) return;
        // An older backend without modules answers 404: nothing pending. Anything else is said.
        if (err instanceof ApiError && err.status === 404) setAcciones({ caseId, items: [] });
        else setError(`No se pudieron cargar las acciones pendientes. ${errorMessage(err)}`);
      },
    );
    return () => controller.abort();
  }, [caseId, accionesTick]);

  const items = acciones && acciones.caseId === caseId ? acciones.items : [];
  const nombres = useMemo(() => Object.fromEntries((datos?.modulos ?? []).map((m) => [m.clave, m.nombre])), [datos]);
  const hechos = useMemo(() => new Set((datos?.modulos ?? []).flatMap((m) => m.hechos.map((h) => h.nombre))), [datos]);
  const recargarAcciones = useCallback(() => setAccionesTick((n) => n + 1), []);
  const recargar = useCallback(() => {
    setTick((n) => n + 1);
    setAccionesTick((n) => n + 1);
  }, []);

  async function cambiar(clave: string, activo: boolean, borrar: boolean): Promise<boolean> {
    try {
      setDatos(await setModulo(clave, activo, borrar));
      setError(null);
      // Its notices, actions (and, when erased, its facts) changed on the server.
      alCambiar();
      setAccionesTick((n) => n + 1);
      if (borrar && caseId) aplicarCaso(await getCase({ caseId }));
      return true;
    } catch (err) {
      setError(`No se pudo cambiar el módulo. ${errorMessage(err)}`);
      return false;
    }
  }

  async function decidir(id: string, confirmar: boolean): Promise<void> {
    try {
      const accion = confirmar ? await confirmarAccion(id) : await rechazarAccion(id);
      setError(accion.estado === "fallida" ? `La acción no se pudo completar: ${accion.resultado ?? ""}` : null);
    } catch (err) {
      setError(`No se pudo ${confirmar ? "confirmar" : "rechazar"} la acción. ${errorMessage(err)}`);
    }
    setAccionesTick((n) => n + 1);
  }

  return {
    datos,
    error,
    nombres,
    hechos,
    cambiar,
    recargar,
    acciones: items,
    pendientes: items.filter((a) => a.estado === "pendiente").length,
    decidir,
    recargarAcciones,
  };
}
