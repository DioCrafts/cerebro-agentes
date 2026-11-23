// A council session followed live over its Server-Sent Events stream (no polling): the server sends
// the session each time it changes and ends the stream once the council is no longer working (or is
// waiting for the user's confirmation). `recargar` opens it again, e.g. after confirming.
//
// Reconnection, back-off and the «never show a stale session under a new id» rule live in useFlujoSse.

import { useState } from "react";

import { isSesion, rutaEventosSesion, type SesionConsejo } from "../apiConsejo";
import { useFlujoSse } from "./useFlujoSse";

export { esperaReintento } from "../lib/sse";
export { reintentable } from "./useFlujoSse";

export function useSesionConsejo(
  caseId: string | null,
  sesionId: string | null,
): { sesion: SesionConsejo | null; error: string | null; recargar: () => void } {
  const [reinicio, setReinicio] = useState(0);
  const ruta = caseId && sesionId ? rutaEventosSesion(caseId, sesionId) : null;
  const { datos, error } = useFlujoSse(ruta, "sesion", isSesion, reinicio);
  return { sesion: datos, error, recargar: () => setReinicio((n) => n + 1) };
}
