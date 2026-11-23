// Install hint. On iPhone/iPad Safari there is no install prompt, and notices on the phone only work for an
// app added to the home screen (iOS/iPadOS 16.4 or later): say how. Where the browser offers its own prompt
// (Chrome, Edge, Samsung Internet on Android; Chrome/Edge on a computer), offer an «Instalar» button.
// Dismissed for 30 days.

import { useState } from "react";

import { enModoApp, esIOS, instalar, usePwa } from "../lib/pwa";
import { Button, Flash, IconButton } from "../ui";
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
  const { instalable } = usePwa();
  const [descartado, setDescartado] = useState(descartadoRecientemente);
  const ios = esIOS() && !enModoApp();
  if (descartado || enModoApp() || (!ios && !instalable)) return null;

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
          <strong>Instala HaciendaGPT en este dispositivo.</strong>{" "}
          {ios ? <InstruccionesIOS /> : "Se abre como una aplicación y puede avisarte de lo que vence."}
        </p>
        {!ios && (
          <Button size="small" variant="primary" onClick={() => void instalar()}>
            Instalar
          </Button>
        )}
        <IconButton icon={XIcon} aria-label="Ahora no" variant="invisible" size="small" onClick={descartar} />
      </Flash>
    </aside>
  );
}
