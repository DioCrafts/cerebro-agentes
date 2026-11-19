// «Mensajería»: talk to the household assistant from SimpleX Chat (pair a chat, see and unpair the paired ones).

import { MensajeriaPanel } from "../components/MensajeriaPanel";
import type { PropsPanel } from "./tipos";

export default function PanelMensajeria({ ctx, open, onClose }: PropsPanel) {
  return <MensajeriaPanel open={open} onClose={onClose} caseId={ctx.caso.id} />;
}
