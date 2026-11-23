// Install hint for iPhone/iPad Safari, which has no install prompt: notices on the phone only work for an
// app added to the home screen (iOS/iPadOS 16.4 or later), so say how. Dismissed for 30 days.
//
// Where the browser offers its own prompt (Chrome, Edge, Samsung Internet on Android; Chrome/Edge on a
// computer) the offer is a button in the header (CabeceraGlobal), not this banner: the prompt arrives after
// the first paint (`beforeinstallprompt`), and a banner inserted above the content then would push the whole
// page down (a layout shift of 0.128 on a phone). On iOS this is known from the first render, so it moves
// nothing.

import { useState } from "react";

import { enModoApp, esIOS } from "../lib/pwa";
import { Flash, IconButton } from "../ui";
import { DesktopDownloadIcon, ShareIcon, XIcon } from "../ui/iconos";

const CLAVE = "haciendagpt_instalar_descartado";
const DIAS = 30;

function descartadoRecientemente(): boolean {
  try {
    const cuando = Number(localStorage.getItem(CLAVE) ?? "0");
    return Date.now() - cuando < DIAS * 24 * 3600 * 1000;
  } catch {
    return false;
  }
}

/** The iOS instructions (also used by «Avisos en el móvil»). */
export function InstruccionesIOS() {
  return (
    <span>
      En Safari, pulsa <ShareIcon size={14} aria-label="Compartir" className="inline align-[-2px]" /> <strong>Compartir</strong> y luego{" "}
      <strong>«Añadir a pantalla de inicio»</strong>. En iPhone y iPad los avisos en el móvil solo funcionan con la app añadida a la
      pantalla de inicio (iOS 16.4 o posterior).
    </span>
  );
}

export function AvisoInstalar() {
  const [descartado, setDescartado] = useState(descartadoRecientemente);
  if (descartado || enModoApp() || !esIOS()) return null;

  function descartar() {
    try {
      localStorage.setItem(CLAVE, String(Date.now()));
    } catch {
      // Not remembered: it shows again next time.
    }
    setDescartado(true);
  }

  return (
    <aside aria-label="Instalar la aplicación" className="px-4 pt-4 md:px-6">
      <Flash className="flex items-start gap-3">
        <DesktopDownloadIcon className="mt-0.5 shrink-0" />
        <p className="m-0 min-w-0 flex-1">
          <strong>Instala Cerebro en este dispositivo.</strong>{" "}
          <InstruccionesIOS />
        </p>
        <IconButton icon={XIcon} aria-label="Ahora no" variant="invisible" size="small" onClick={descartar} />
      </Flash>
    </aside>
  );
}
