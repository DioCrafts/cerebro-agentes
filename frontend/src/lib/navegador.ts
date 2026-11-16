// Whether the browser agent is offered by this service (the operator's kill switch), to show its entry
// point only when it can be used. One request per sign-in; any error means "not offered".

import { useEffect, useState } from "react";

import { getEstadoNavegador } from "../apiNavegador";

export function useNavegadorActivo(enabled: boolean): boolean {
  const [activo, setActivo] = useState(false);
  useEffect(() => {
    if (!enabled) return;
    const ctrl = new AbortController();
    getEstadoNavegador(ctrl.signal).then(
      (e) => setActivo(e.activo),
      () => setActivo(false),
    );
    return () => ctrl.abort();
  }, [enabled]);
  return enabled && activo;
}
