// The user's consultations (the conversation history in this browser), as GitHub's dashboard sidebar:
// a heading with the primary «Nueva» button, a NavList with the current one marked, what the advisor
// covers and the disclaimer. Shown in the PageLayout pane on a computer and in a side sheet on a phone.

import { relativeTime } from "../lib/presentation";
import { TOPICS } from "../lib/mock";
import type { Conversation } from "../lib/storage";
import { ActionList, Button, Heading, Label, NavList } from "../ui";
import { CommentDiscussionIcon, InfoIcon, PlusIcon, TrashIcon } from "../ui/iconos";

interface ListaConsultasProps {
  conversations: Conversation[];
  activeId: string;
  onSelect: (id: string) => void;
  onDelete: (id: string) => void;
  onNewConsultation: () => void;
}

export function ListaConsultas({ conversations, activeId, onSelect, onDelete, onNewConsultation }: ListaConsultasProps) {
  return (
    <div className="flex flex-col gap-6">
      <section aria-labelledby="titulo-consultas" className="flex flex-col gap-2">
        <div className="flex items-center justify-between gap-2">
          <Heading as="h2" variant="small" id="titulo-consultas">
            Consultas
          </Heading>
          <Button variant="primary" size="small" leadingVisual={PlusIcon} onClick={onNewConsultation}>
            Nueva
          </Button>
        </div>
        {conversations.length === 0 ? (
          <p className="m-0 text-fg-muted">Tus consultas aparecerán aquí y se conservan en este navegador.</p>
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
                {c.title}
                <ActionList.Description variant="block">{relativeTime(c.updatedAt)}</ActionList.Description>
                <NavList.TrailingAction label={`Eliminar la consulta «${c.title}»`} icon={TrashIcon} onClick={() => onDelete(c.id)} />
              </NavList.Item>
            ))}
          </NavList>
        )}
      </section>

      <section aria-labelledby="titulo-cubre" className="flex flex-col gap-2 border-t border-border-muted pt-4">
        <Heading as="h2" variant="small" id="titulo-cubre">
          Qué cubre
        </Heading>
        <ul className="m-0 flex list-none flex-wrap gap-1 p-0">
          {TOPICS.map((t) => (
            <li key={t}>
              <Label variant="accent">{t}</Label>
            </li>
          ))}
        </ul>
      </section>

      <p className="m-0 flex gap-2 border-t border-border-muted pt-4 text-xs text-fg-muted">
        <InfoIcon className="mt-0.5 shrink-0" />
        <span>
          <strong className="text-fg-default">Herramienta de apoyo.</strong> No sustituye el asesoramiento fiscal profesional ni el
          criterio oficial.
        </span>
      </p>
    </div>
  );
}
