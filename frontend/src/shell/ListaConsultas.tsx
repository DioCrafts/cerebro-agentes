// The user's conversations (the history in this browser), as GitHub's dashboard sidebar: a heading with the
// primary «Nueva» button, a NavList with the current one marked, the areas Cerebro looks after now
// (src/lib/areas.ts) and the disclaimer. Shown in the PageLayout pane on a computer and in a side sheet on a phone.

import { relativeTime } from "../lib/presentation";
import type { Conversation } from "../lib/storage";
import { Button, Heading, Label, NavList, VisuallyHidden } from "../ui";
import { CommentDiscussionIcon, InfoIcon, PlusIcon, TrashIcon } from "../ui/iconos";

interface ListaConsultasProps {
  conversations: Conversation[];
  activeId: string;
  onSelect: (id: string) => void;
  onDelete: (id: string) => void;
  onNewConsultation: () => void;
  /** The areas that can be used now (their names). */
  areas: string[];
}

export function ListaConsultas({ conversations, activeId, onSelect, onDelete, onNewConsultation, areas }: ListaConsultasProps) {
  return (
    <div className="flex flex-col gap-6">
      <section aria-labelledby="titulo-consultas" className="flex flex-col gap-2">
        <div className="flex items-center justify-between gap-2">
          <Heading as="h2" variant="small" id="titulo-consultas">
            Conversaciones
          </Heading>
          <Button variant="primary" size="small" leadingVisual={PlusIcon} onClick={onNewConsultation}>
            Nueva
          </Button>
        </div>
        {conversations.length === 0 ? (
          <p className="m-0 text-fg-muted">Tus conversaciones aparecerán aquí y se guardan en este navegador.</p>
        ) : (
          <NavList aria-labelledby="titulo-consultas">
            {conversations.map((c) => (
              <NavList.Item
                key={c.id}
                as="button"
                aria-current={c.id === activeId ? "page" : undefined}
                onClick={() => onSelect(c.id)}
              >
                <NavList.LeadingVisual>
                  <CommentDiscussionIcon />
                </NavList.LeadingVisual>
                {/* The date inside the label, not as a Description: the item's accessible name must contain all
                    its visible text (WCAG 2.5.3; Primer names it with the label only). */}
                {c.title}
                <span className="block text-xs text-fg-muted">
                  <VisuallyHidden>, </VisuallyHidden>
                  {relativeTime(c.updatedAt)}
                </span>
                <NavList.TrailingAction label={`Eliminar la conversación «${c.title}»`} icon={TrashIcon} onClick={() => onDelete(c.id)} />
              </NavList.Item>
            ))}
          </NavList>
        )}
      </section>

      <section aria-labelledby="titulo-mis-areas" className="flex flex-col gap-2 border-t border-border-muted pt-4">
        <Heading as="h2" variant="small" id="titulo-mis-areas">
          Tus áreas
        </Heading>
        <ul className="m-0 flex list-none flex-wrap gap-1 p-0">
          {areas.map((t) => (
            <li key={t}>
              <Label variant="accent">{t}</Label>
            </li>
          ))}
        </ul>
        {/* Inside the section, so it belongs to a landmark like the rest of the page. */}
        <p className="m-0 mt-2 flex gap-2 border-t border-border-muted pt-4 text-xs text-fg-muted">
          <InfoIcon className="mt-0.5 shrink-0" />
          <span>
            <strong className="text-fg-default">Herramienta de apoyo.</strong> En decisiones con consecuencias legales, fiscales o de
            salud, contrasta también con un profesional.
          </span>
        </p>
      </section>
    </div>
  );
}
