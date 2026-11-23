// What the advisor is doing / did for one answer. Live, it replaces the old silent "Consultando…" with the actual
// searches as they happen; afterwards it collapses into a "how I got here" disclosure under the answer.
//
// Design system (docs/ui/sistema-de-diseno.md): the steps are a condensed Primer Timeline, like the events between
// the comments of a pull request; the record under an answer is a Details.

import { useEffect, useState } from "react";

import type { ProgressEvent } from "../api";
import { type ActivityRow, activityRows, formatElapsed, statusLine } from "../lib/presentation";
import { Details, Label, Spinner, Timeline } from "../ui";
import { ListaTimeline, PasoTimeline } from "./conversacion/ListaTimeline";
import { ChecklistIcon, ChevronRightIcon, NumberIcon, PencilIcon, PeopleIcon, SearchIcon, ShieldCheckIcon } from "../ui/iconos";
import { RichText } from "./RichText";

const ROW_ICON = { search: SearchIcon, case: ChecklistIcon, calc: NumberIcon, team: PeopleIcon, verify: ShieldCheckIcon, revise: PencilIcon } as const;

function Rows({ rows, etiqueta }: { rows: ActivityRow[]; etiqueta: string }) {
  return (
    <ListaTimeline etiqueta={etiqueta}>
      {rows.map((row, i) => {
        const Icon = ROW_ICON[row.kind];
        return (
          <PasoTimeline key={i}>
            <Timeline.Badge>
              <Icon />
            </Timeline.Badge>
            <Timeline.Body>
              <span className="text-fg-default">
                {row.kind === "search" && !row.text.startsWith("¿") ? `Búsqueda: «${row.text}»` : row.text}
              </span>
              {row.outcome && <span className={row.failed ? "text-fg-danger" : "text-fg-muted"}> — {row.outcome}</span>}
              {row.detail && <span className="block text-xs text-fg-muted">{row.detail}</span>}
            </Timeline.Body>
          </PasoTimeline>
        );
      })}
    </ListaTimeline>
  );
}

/** Live view while the turn is running. */
interface LiveActivityProps {
  events: ProgressEvent[];
  startedAt: number;
  // The answer as it is being written. It has NOT been verified yet.
  draft: string;
}

export function LiveActivity({ events, startedAt, draft }: LiveActivityProps) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);
  const rows = activityRows(events);
  const last = events[events.length - 1];
  const checking = last?.type === "verifying" || last?.type === "revising";

  return (
    <div className="flex flex-col gap-3">
      <p className="m-0 flex items-center gap-2 font-semibold text-fg-default" aria-live="polite">
        <Spinner size="small" srText={null} />
        <span className="flex-1">{draft && !checking ? "Redactando la respuesta…" : statusLine(events)}</span>
        <span className="font-mono text-xs font-normal tabular-nums text-fg-muted">{formatElapsed(now - startedAt)}</span>
      </p>
      {rows.length > 0 && <Rows rows={rows} etiqueta="Lo que estoy haciendo" />}
      {draft ? (
        <div className="border-t border-border-muted pt-3">
          <Label variant="attention" className="mb-2">
            <PencilIcon size={12} className="mr-1" />
            {checking ? "Borrador · comprobándolo contra las fuentes" : "Borrador · aún sin verificar"}
          </Label>
          {/* aria-hidden: a screen reader should hear the final, verified answer once, not every token. */}
          <div aria-hidden className="opacity-80">
            <RichText text={draft} draft />
          </div>
        </div>
      ) : (
        <p className="m-0 text-xs text-fg-muted">
          Busco en la normativa y compruebo cada afirmación antes de responder; puede tardar uno o dos minutos (algo más si la consulta es compleja).
        </p>
      )}
    </div>
  );
}

/** Collapsed record under a finished answer. */
export function ActivitySummary({ rows, elapsedMs }: { rows: ActivityRow[]; elapsedMs: number }) {
  if (rows.length === 0) return null;
  const searches = rows.filter((r) => r.kind === "search").length;
  const label = [
    searches > 0 ? (searches === 1 ? "1 búsqueda" : `${searches} búsquedas`) : null,
    rows.some((r) => r.kind === "team") ? "con equipo de agentes" : null,
    rows.some((r) => r.kind === "revise") ? "1 corrección" : null,
    formatElapsed(elapsedMs),
  ]
    .filter(Boolean)
    .join(" · ");
  return (
    <Details className="group mt-3">
      <Details.Summary className="inline-flex cursor-pointer items-center gap-1 text-xs text-fg-muted hover:text-fg-accent">
        <ChevronRightIcon size={12} className="transition-transform group-open:rotate-90" />
        Cómo llegué a esta respuesta · {label}
      </Details.Summary>
      <div className="mt-2 pl-1">
        <Rows rows={rows} etiqueta="Cómo llegué a esta respuesta" />
      </div>
    </Details>
  );
}
