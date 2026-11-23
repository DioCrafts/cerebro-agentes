// PaginaPanel: a panel shown as a page in the main column (GitHub's settings or repository pages): Primer's
// PageHeader (icon, title, description, actions) over the content. When it opens, focus moves to the page
// so a screen reader announces it.
//
//   <PaginaPanel titulo="Tu expediente" icono={FileIcon} descripcion="Lo que sé de tu situación" activa={open}>
//     …
//   </PaginaPanel>

import type { Icon } from "@primer/octicons-react";
import { PageHeader } from "@primer/react";
import type { ReactNode } from "react";
import { useEffect, useRef } from "react";

export interface PaginaPanelProps {
  titulo: string;
  descripcion?: ReactNode;
  icono?: Icon;
  acciones?: ReactNode;
  /** The page is on screen: focus moves to it the moment it becomes so. */
  activa: boolean;
  children: ReactNode;
}

export function PaginaPanel({ titulo, descripcion, icono: Icono, acciones, activa, children }: PaginaPanelProps) {
  const ref = useRef<HTMLElement | null>(null);
  useEffect(() => {
    if (activa) ref.current?.focus({ preventScroll: false });
  }, [activa]);
  return (
    <section ref={ref} tabIndex={-1} aria-label={titulo} className="flex flex-col gap-4 outline-none">
      <PageHeader hasBorder>
        <PageHeader.TitleArea>
          {Icono && (
            <PageHeader.LeadingVisual>
              <Icono size={24} />
            </PageHeader.LeadingVisual>
          )}
          <PageHeader.Title as="h2">{titulo}</PageHeader.Title>
        </PageHeader.TitleArea>
        {acciones && <PageHeader.Actions>{acciones}</PageHeader.Actions>}
        {descripcion && <PageHeader.Description>{descripcion}</PageHeader.Description>}
      </PageHeader>
      <div>{children}</div>
    </section>
  );
}
