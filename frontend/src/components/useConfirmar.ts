// Primer's useConfirm with the buttons in Spanish and the danger style by default (see ZonaPeligro.tsx).

import type { ReactNode } from "react";
import { useCallback } from "react";

import { useConfirm } from "../ui";

/** `confirmar(título, explicación, botón)` → true if the user pressed the button. */
export function useConfirmar() {
  const confirm = useConfirm();
  return useCallback(
    (titulo: string, contenido: ReactNode, boton: string, tipo: "danger" | "primary" = "danger") =>
      confirm({ title: titulo, content: contenido, confirmButtonContent: boton, cancelButtonContent: "Cancelar", confirmButtonType: tipo }),
    [confirm],
  );
}
