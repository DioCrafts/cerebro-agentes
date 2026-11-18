// «Lo que sé de ti»: the conversation memory (what the assistant remembers, with the user's own words), and the
// earlier conversations with their summaries. Works with a case, or with the signed-in account alone.

import { RecuerdosPanel } from "../components/RecuerdosPanel";
import type { PropsPanel } from "./tipos";

export default function PanelRecuerdos({ ctx, open, onClose }: PropsPanel) {
  return <RecuerdosPanel open={open} onClose={onClose} caseId={ctx.caso.id} />;
}
