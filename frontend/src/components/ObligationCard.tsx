// Obligation card: a tax obligation the rules engine thinks may apply to the user, with the thing people actually
// need first — the deadline. A Box: title and risk Label in the header, deadline and legal basis in the body, the
// certainty in the footer.

import type { ObligationCandidate } from "../api";
import { RISK_LABEL, daysUntil, formatDate, missingFactsSentence, riskBucket } from "../lib/presentation";
import { Caja, CounterLabel, Label } from "../ui";
import { CalendarIcon } from "../ui/iconos";

const VARIANTE_RIESGO = { high: "danger", medium: "attention", low: "secondary" } as const;

function deadlineCopy(iso: string): { text: string; urgent: boolean } {
  const days = daysUntil(iso);
  const date = formatDate(iso) ?? iso;
  if (days === null) return { text: date, urgent: false };
  if (days < 0) return { text: `${date} · plazo vencido`, urgent: true };
  if (days === 0) return { text: `${date} · vence hoy`, urgent: true };
  if (days <= 30) return { text: `${date} · quedan ${days} ${days === 1 ? "día" : "días"}`, urgent: true };
  return { text: date, urgent: false };
}

export function ObligationCard({ obligation }: { obligation: ObligationCandidate }) {
  const risk = riskBucket(obligation.risk_level);
  const missing = missingFactsSentence(obligation.blocking_missing_facts);
  const sources = obligation.evidence_refs.map((e) => e.title).filter((t): t is string => Boolean(t));
  const deadline = obligation.due_date ? deadlineCopy(obligation.due_date) : null;

  return (
    <Caja as="article" aria-label={obligation.title}>
      <Caja.Cabecera className="flex-wrap py-2!">
        <h5 className="m-0 min-w-0 flex-1 text-sm font-semibold">{obligation.title}</h5>
        <Label variant={VARIANTE_RIESGO[risk]}>{RISK_LABEL[risk]}</Label>
      </Caja.Cabecera>
      <Caja.Cuerpo className="flex flex-col gap-3">
        <p className="m-0 text-fg-default">{obligation.description}</p>
        {deadline && (
          <div>
            <p className={`m-0 flex items-center gap-2 font-semibold ${deadline.urgent ? "text-fg-danger" : "text-fg-default"}`}>
              <CalendarIcon />
              Fecha límite: {deadline.text}
            </p>
            {obligation.due_date_note && <p className="m-0 mt-1 pl-6 text-xs text-fg-muted">{obligation.due_date_note}</p>}
          </div>
        )}
        {(missing || sources.length > 0) && (
          <dl className="m-0 flex flex-col gap-1 border-t border-border-muted pt-3">
            {missing && (
              <div>
                <dt className="sr-only">Datos pendientes</dt>
                <dd className="m-0 text-fg-default">{missing}</dd>
              </div>
            )}
            {sources.length > 0 && (
              <div className="flex flex-col gap-0.5 sm:flex-row sm:gap-2">
                <dt className="shrink-0 text-fg-muted">Base normativa</dt>
                <dd className="m-0 text-fg-default">{sources.join(" · ")}</dd>
              </div>
            )}
          </dl>
        )}
      </Caja.Cuerpo>
      <Caja.Pie className="py-2! text-xs text-fg-muted">
        Indicio detectado a partir de tus datos (certeza estimada {Math.round(obligation.confidence * 100)} %). Confírmalo antes de actuar.
      </Caja.Pie>
    </Caja>
  );
}

interface ObligationCardListProps {
  obligations: ObligationCandidate[];
}

export function ObligationCardList({ obligations }: ObligationCardListProps) {
  if (obligations.length === 0) return null;
  return (
    <section className="mt-6 flex flex-col gap-2" aria-label="Obligaciones detectadas">
      <h4 className="m-0 flex items-center gap-2 text-sm font-semibold text-fg-default">
        {obligations.length === 1 ? "Posible obligación" : "Posibles obligaciones"}
        {obligations.length > 1 && <CounterLabel>{obligations.length}</CounterLabel>}
      </h4>
      {obligations.map((o) => (
        <ObligationCard key={o.obligation_id} obligation={o} />
      ))}
    </section>
  );
}
