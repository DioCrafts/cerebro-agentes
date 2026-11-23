// «Lo que sé de ti»: the conversation memory (what the assistant remembers, with the user's own words), and the
// earlier conversations with their summaries. Works with a case, or with the signed-in account alone.
// Shown in a side sheet (registro.ts).

import { RecuerdosPanel } from "../components/RecuerdosPanel";
import type { PropsPanel } from "./tipos";

export default function PanelRecuerdos({ ctx, open }: PropsPanel) {
  return <RecuerdosPanel open={open} caseId={ctx.caso.id} />;
}
