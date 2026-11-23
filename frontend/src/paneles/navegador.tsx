// «Agente con navegador» (only when the operator switched it on: flag `navegador`).

import { NavegadorPanel } from "../components/NavegadorPanel";
import type { PropsPanel } from "./tipos";

export default function PanelNavegador({ ctx, open }: PropsPanel) {
  return (
    <NavegadorPanel
      open={open}
      caseId={ctx.caso.id}
      onChanged={() => {
        // An import wrote facts; a procedure left a pending action.
        ctx.acciones.recargar();
        ctx.recargarCaso();
      }}
    />
  );
}
