// How far an answer can be trusted, in one line plus a plain-language explanation. In GitHub's terms it is the
// state of the answer, so it is a StateLabel (green: verified; amber: partly or not verified; grey: needs a datum).

import type { AnswerMode } from "../api";
import { TRUST } from "../lib/presentation";
import type { Icon } from "../ui/iconos";
import { AlertIcon, CheckCircleIcon, QuestionIcon, ShieldIcon } from "../ui/iconos";
import { EtiquetaEstado, type TonoEstado } from "./conversacion/EtiquetaEstado";

const ESTILO: Record<"cited" | "uncited" | "abstain", { tono: TonoEstado; icono: Icon }> = {
  cited: { tono: "exito", icono: CheckCircleIcon },
  uncited: { tono: "atencion", icono: AlertIcon },
  abstain: { tono: "neutro", icono: ShieldIcon },
};

interface TrustStripProps {
  mode: AnswerMode;
  // e.g. "4 de 5 afirmaciones verificadas"; replaces the generic explanation.
  detail?: string | null;
}

export function TrustStrip({ mode, detail }: TrustStripProps) {
  const copy = TRUST[mode];
  const estilo = ESTILO[copy.cls];
  const Icono = mode === "clarification" ? QuestionIcon : estilo.icono;
  return (
    <div role="status" className="mt-4 flex flex-col gap-1">
      <p className="m-0 flex flex-wrap items-center gap-2">
        <EtiquetaEstado tono={estilo.tono} size="medium" visual={<Icono />}>
          {copy.label}
        </EtiquetaEstado>
        {detail && <span className="font-semibold text-fg-default">{detail}</span>}
      </p>
      <p className="m-0 text-xs text-fg-muted">{copy.short}</p>
    </div>
  );
}
