// Claim-by-claim verification: what the independent checker concluded about each normative statement in the answer,
// in plain language. Problems are open by default; a fully verified answer keeps the detail one click away.
//
// A folding Box (like a check run's details); each claim is a row with its verdict as a StateLabel and the sources
// it was checked against as small buttons that jump to the source card.

import type { ClaimCheck, ClaimVerdict } from "../api";
import { VERDICT_LABEL } from "../lib/presentation";
import { Button, Flash, Label } from "../ui";
import type { Icon } from "../ui/iconos";
import { AlertIcon, CheckCircleIcon, XCircleIcon } from "../ui/iconos";
import { DetallesCaja } from "./conversacion/DetallesCaja";
import { EtiquetaEstado, type TonoEstado } from "./conversacion/EtiquetaEstado";

const VERDICT_STYLE: Record<ClaimVerdict, { icon: Icon; tono: TonoEstado }> = {
  respaldada: { icon: CheckCircleIcon, tono: "exito" },
  parcial: { icon: AlertIcon, tono: "atencion" },
  no_respaldada: { icon: XCircleIcon, tono: "peligro" },
};

interface VerificationPanelProps {
  claims: ClaimCheck[];
  // Warnings that are not about a specific claim (e.g. verifier unavailable).
  otherWarnings: string[];
  onCite: (fragmentId: string) => void;
}

export function VerificationPanel({ claims, otherWarnings, onCite }: VerificationPanelProps) {
  if (claims.length === 0 && otherWarnings.length === 0) return null;
  const problems = claims.filter((c) => c.verdict !== "respaldada");
  // Problems first: that is what the reader must act on.
  const ordered = [...problems, ...claims.filter((c) => c.verdict === "respaldada")];

  return (
    <div className="mt-3 flex flex-col gap-2">
      {otherWarnings.map((warning, i) => (
        <Flash key={`w-${i}`} variant="warning">
          <span className="flex gap-2">
            <AlertIcon className="mt-0.5 shrink-0" />
            {warning}
          </span>
        </Flash>
      ))}
      {claims.length > 0 && (
        <DetallesCaja
          abierto={problems.length > 0}
          resumen="Comprobación afirmación por afirmación"
          extra={problems.length > 0 ? <Label variant="danger">{problems.length === 1 ? "1 a revisar" : `${problems.length} a revisar`}</Label> : undefined}
        >
          <ul className="m-0 list-none divide-y divide-border-muted p-0" aria-label="Afirmaciones comprobadas">
            {ordered.map((claim, i) => {
              const style = VERDICT_STYLE[claim.verdict];
              const Icono = style.icon;
              return (
                <li key={i} className="flex flex-col gap-1 px-4 py-3">
                  <p className="m-0 text-fg-default">{claim.text}</p>
                  <p className="m-0 flex flex-wrap items-center gap-2">
                    <EtiquetaEstado tono={style.tono} visual={<Icono size={12} />}>
                      {VERDICT_LABEL[claim.verdict]}
                    </EtiquetaEstado>
                    {claim.fragment_ids.map((id) => (
                      <Button key={id} size="small" variant="invisible" onClick={() => onCite(id)} aria-label={`Ver la fuente ${id}`} className="font-mono">
                        {id}
                      </Button>
                    ))}
                  </p>
                  {claim.verdict !== "respaldada" && claim.reason && <p className="m-0 text-xs text-fg-muted">{claim.reason}</p>}
                </li>
              );
            })}
          </ul>
        </DetallesCaja>
      )}
    </div>
  );
}
