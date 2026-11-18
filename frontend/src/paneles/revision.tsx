// «Revisión de casos»: the masked candidate cases made from the assistant's mistakes, to accept as private
// regression tests or to reject (and the per-case «aprender de los fallos» switch).

import { RevisionCasosPanel } from "../components/RevisionCasosPanel";
import type { PropsPanel } from "./tipos";

export default function PanelRevision({ ctx, open, onClose }: PropsPanel) {
  return <RevisionCasosPanel open={open} onClose={onClose} caseId={ctx.caso.id} />;
}
