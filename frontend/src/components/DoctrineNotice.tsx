// Validity of the doctrine an answer relies on: shown when the question has no settled criterion, or when a cited
// source has been superseded or is disputed. A warning Flash inside the answer's comment.

import { Flash } from "../ui";
import { LawIcon } from "../ui/iconos";

interface DoctrineNoticeProps {
  conflicts: string[];
  unsettled: boolean;
}

export function DoctrineNotice({ conflicts, unsettled }: DoctrineNoticeProps) {
  if (!unsettled && conflicts.length === 0) return null;
  const titulo = unsettled ? "No hay criterio firme sobre esta cuestión" : "Atención a la vigencia de las fuentes";
  return (
    <Flash variant="warning" role="note" aria-label={titulo} className="mt-3">
      <div className="flex gap-2">
        <LawIcon className="mt-0.5 shrink-0" />
        <div className="flex min-w-0 flex-col gap-1">
          <p className="m-0 font-semibold">{titulo}</p>
          <p className="m-0">
            {unsettled
              ? "Las fuentes discrepan o el criterio está discutido. La respuesta expone las posturas; ninguna es segura hasta que un tribunal o la Administración lo resuelva."
              : "Alguna de las fuentes citadas ha sido superada, matizada o discutida por resoluciones posteriores."}
          </p>
          {conflicts.length > 0 && (
            <ul className="m-0 list-disc pl-5">
              {conflicts.map((conflict, i) => (
                <li key={i}>{conflict}</li>
              ))}
            </ul>
          )}
          <p className="m-0 text-xs text-fg-muted">
            La vigencia se deduce de las citas entre resoluciones del corpus: es un indicio, no una declaración oficial.
          </p>
        </div>
      </div>
    </Flash>
  );
}
