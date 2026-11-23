// Source card: one per fragment the answer cites. Shows who issued it, when, whether it binds the Administration,
// and the literal passage — and is the jump target of the [F#] chips in the answer. A Box with its header (fragment
// id, title, labels) and the quoted passage in the body.

import type { Citation } from "../api";
import { authorityLabel, classifySource, formatDate, hostLabel, isHttpUrl, sourceAnchorId } from "../lib/presentation";
import { Caja, CounterLabel, Label, Link } from "../ui";
import { BookIcon, BookmarkIcon, FileIcon, LawIcon, LinkExternalIcon, NumberIcon, QuoteIcon } from "../ui/iconos";

const SOURCE_ICON = {
  boe: FileIcon, // legislation
  manual: BookIcon, // AEAT handbook / informative page
  dgt: QuoteIcon, // DGT binding consulta
  court: LawIcon, // TEAC / TS / TC
  calc: NumberIcon, // deterministic IRPF computation
  other: BookmarkIcon,
} as const;

// Only statuses that should make the reader cautious get a label.
const VIGENCIA_LABEL: Record<string, { label: string; variant: "danger" | "attention" }> = {
  superada: { label: "Superada", variant: "danger" },
  discutida: { label: "Criterio discutido", variant: "attention" },
  matizada: { label: "Matizada", variant: "attention" },
};

interface SourceCardProps {
  citation: Citation;
  scope: string;
  highlighted: boolean;
}

function SourceCard({ citation, scope, highlighted }: SourceCardProps) {
  const vigencia = citation.vigencia ? VIGENCIA_LABEL[citation.vigencia] : undefined;
  const Icon = SOURCE_ICON[classifySource(citation)];
  const issuer = authorityLabel(citation.authority);
  const date = formatDate(citation.date);
  const meta = [issuer, citation.reference, date].filter(Boolean).join(" · ");
  const section = citation.section && citation.section !== "general" && citation.section !== citation.title;

  return (
    <Caja
      as="article"
      aria-label={citation.fragment_id ? `${citation.fragment_id}: ${citation.title}` : citation.title}
      id={citation.fragment_id ? sourceAnchorId(scope, citation.fragment_id) : undefined}
      className={highlighted ? "scroll-mt-24 animate-flash border-border-accent!" : "scroll-mt-24"}
    >
      <Caja.Cabecera className="flex-wrap py-2!">
        <Icon className="shrink-0 text-fg-muted" />
        {citation.fragment_id && <Label variant="accent" className="font-mono">{citation.fragment_id}</Label>}
        <h5 className="m-0 min-w-0 flex-1 text-sm font-semibold">
          {citation.title}
          {section && <span className="font-normal text-fg-muted"> · {citation.section}</span>}
        </h5>
      </Caja.Cabecera>
      <Caja.Cuerpo className="flex flex-col gap-2">
        {(meta || citation.binding != null || vigencia) && (
          <p className="m-0 flex flex-wrap items-center gap-2 text-xs text-fg-muted">
            {meta && <span>{meta}</span>}
            {citation.binding === true && <Label variant="success">Vinculante</Label>}
            {vigencia && (
              <Label variant={vigencia.variant} title={citation.vigencia_resumen ?? undefined}>
                {vigencia.label}
              </Label>
            )}
            {citation.binding === false && <Label variant="secondary">No vinculante</Label>}
          </p>
        )}
        {vigencia && citation.vigencia_resumen && <p className="m-0 text-xs text-fg-default">{citation.vigencia_resumen}</p>}
        {citation.snippet && (
          <blockquote className="m-0 border-l-4 border-border-default pl-3 text-fg-muted">{citation.snippet}</blockquote>
        )}
        {isHttpUrl(citation.locator) && (
          <Link href={citation.locator} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1 self-start text-xs">
            Abrir en {hostLabel(citation.locator)}
            <LinkExternalIcon size={12} />
          </Link>
        )}
      </Caja.Cuerpo>
    </Caja>
  );
}

interface SourceCardListProps {
  citations: Citation[];
  scope: string;
  highlightedId: string | null;
}

export function SourceCardList({ citations, scope, highlightedId }: SourceCardListProps) {
  if (citations.length === 0) return null;
  return (
    <section className="mt-5 flex flex-col gap-2" aria-label="Fuentes citadas">
      <h4 className="m-0 flex items-center gap-2 text-sm font-semibold text-fg-default">
        Fuentes citadas <CounterLabel>{citations.length}</CounterLabel>
      </h4>
      {citations.map((c, i) => (
        <SourceCard
          key={c.fragment_id ?? `${c.locator}-${i}`}
          citation={c}
          scope={scope}
          highlighted={!!c.fragment_id && highlightedId === sourceAnchorId(scope, c.fragment_id)}
        />
      ))}
    </section>
  );
}
