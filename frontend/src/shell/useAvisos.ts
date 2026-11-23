// The advisor speaks first: whenever the case or what we know about the user changes, re-run the
// deterministic jobs (deadlines, year-end plan) and load the notifications. No model call is involved.

import { useCallback, useEffect, useState } from "react";

import type { AdvisorNotification, Fact } from "../api";
import { getNotifications, markNotificationRead, runProactive } from "../api";
import { errorMessage } from "./errores";
import { textoDeValor } from "../lib/guards";

export interface Avisos {
  items: AdvisorNotification[];
  cargando: boolean;
  error: string | null;
  sinLeer: number;
  marcarLeido: (id: string) => void;
  recargar: () => void;
}

export function useAvisos(caseId: string | null, facts: Fact[]): Avisos {
  const [notifications, setNotifications] = useState<{ caseId: string; items: AdvisorNotification[] } | null>(null);
  // Key of the last request that finished; while it differs from the current one we are loading.
  const [settled, setSettled] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);

  const factsKey = facts.map((f) => `${f.name}=${textoDeValor(f.value)}`).join("&");
  const key = `${caseId ?? ""}|${factsKey}|${tick}`;
  const cargando = caseId !== null && settled !== key;

  useEffect(() => {
    if (!caseId) return;
    const controller = new AbortController();
    runProactive({ caseId, signal: controller.signal })
      .catch(() => getNotifications({ caseId, signal: controller.signal }))
      .then(
        (resp) => {
          setNotifications({ caseId, items: resp.notifications });
          setError(null);
        },
        (err: unknown) => {
          if (!controller.signal.aborted) setError(`No se pudieron cargar los avisos. ${errorMessage(err)}`);
        },
      )
      .finally(() => {
        if (!controller.signal.aborted) setSettled(key);
      });
    return () => controller.abort();
  }, [caseId, key]);

  const items = notifications && notifications.caseId === caseId ? notifications.items : [];

  const marcarLeido = useCallback(
    (notificationId: string) => {
      if (!caseId) return;
      markNotificationRead({ caseId, notificationId }).then(
        (resp) => setNotifications({ caseId, items: resp.notifications }),
        (err: unknown) => setError(`No se pudo marcar como leído. ${errorMessage(err)}`),
      );
    },
    [caseId],
  );
  const recargar = useCallback(() => setTick((n) => n + 1), []);

  return { items, cargando, error, sinLeer: items.filter((n) => !n.read_at).length, marcarLeido, recargar };
}
