// Primer's Timeline renders an <ol> only behind the feature flag `primer_react_timeline_list_semantics`; without
// it, plain <div>s. These wrappers give the steps list semantics (and a name) whatever the flag says: the outer
// element is the list, each Timeline.Item a listitem. Once the flag is on in ProveedorUI they can go.

import type { ReactNode } from "react";

import { Timeline } from "../../ui";

export function ListaTimeline({ etiqueta, className, vivo, children }: { etiqueta: string; className?: string; vivo?: boolean; children: ReactNode }) {
  return (
    <div role="list" aria-label={etiqueta} aria-live={vivo ? "polite" : undefined} className={className}>
      <Timeline clipSidebar>{children}</Timeline>
    </div>
  );
}

export function PasoTimeline({ children }: { children: ReactNode }) {
  return (
    <Timeline.Item condensed role="listitem">
      {children}
    </Timeline.Item>
  );
}
