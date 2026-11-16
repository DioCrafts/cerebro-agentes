// «Investigar»: research a question with sources (corpus, BOE, and the web with the user's consent).

import { InvestigarPanel } from "../components/InvestigarPanel";
import type { PropsPanel } from "./tipos";

export default function PanelInvestigar({ ctx, open, onClose }: PropsPanel) {
  return <InvestigarPanel key={`investigar-${ctx.conversacionId}`} open={open} onClose={onClose} caseId={ctx.caso.id} />;
}
