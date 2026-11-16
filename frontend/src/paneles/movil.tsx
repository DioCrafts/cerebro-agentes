// «Avisos en el móvil»: Web Push on this device (opt-in) and how to install the app.

import { AvisosMovilPanel } from "../components/AvisosMovilPanel";
import type { PropsPanel } from "./tipos";

export default function PanelMovil({ open, onClose }: PropsPanel) {
  return <AvisosMovilPanel open={open} onClose={onClose} />;
}
