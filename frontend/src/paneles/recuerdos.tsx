// «Lo que sé de ti»: the conversation memory kept by Hindsight (what the assistant remembers, each fact with its
// conversation and date), and the earlier conversations with the user's own messages. Works with a case, or with the
// signed-in account alone.
// Shown in a side sheet (registro.ts).

import { RecuerdosPanel } from "../components/RecuerdosPanel";
import type { PropsPanel } from "./tipos";

export default function PanelRecuerdos({ ctx, open }: PropsPanel) {
  return <RecuerdosPanel open={open} caseId={ctx.caso.id} />;
}
