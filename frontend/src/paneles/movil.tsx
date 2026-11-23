// «Avisos en el móvil»: Web Push on this device (opt-in) and how to install the app. Shown in a side sheet
// (registro.ts).

import { AvisosMovilPanel } from "../components/AvisosMovilPanel";
import type { PropsPanel } from "./tipos";

export default function PanelMovil({ open }: PropsPanel) {
  return <AvisosMovilPanel open={open} />;
}
