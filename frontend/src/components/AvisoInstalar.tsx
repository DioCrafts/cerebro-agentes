// Install hint. On iPhone/iPad Safari there is no install prompt, and notices on the phone only work for an
// app added to the home screen (iOS/iPadOS 16.4 or later): say how. Where the browser offers its own prompt
// (Chrome, Edge, Samsung Internet on Android; Chrome/Edge on a computer), offer an «Instalar» button.
// Dismissed for 30 days.

import { Download, Share, X } from "lucide-react";
import { useState } from "react";

import { enModoApp, esIOS, instalar, usePwa } from "../lib/pwa";

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
      En Safari, pulsa <Share size={14} aria-label="Compartir" className="inline align-[-2px]" /> <strong>Compartir</strong> y luego{" "}
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
    <aside aria-label="Instalar la aplicación" className="flex items-start gap-3 border-b border-line bg-primary-soft px-3 py-2.5 text-[13px] text-ink sm:px-6">
      <Download size={16} aria-hidden className="mt-0.5 shrink-0 text-primary-dark" />
      <p className="min-w-0 flex-1 leading-snug">
        <strong>Instala HaciendaGPT en este dispositivo.</strong>{" "}
        {ios ? <InstruccionesIOS /> : "Se abre como una aplicación y puede avisarte de lo que vence."}
      </p>
      {!ios && (
        <button
          type="button"
          onClick={() => void instalar()}
          className="shrink-0 rounded-lg bg-primary px-3 py-1.5 text-[12.5px] font-semibold text-white hover:bg-primary-hover"
        >
          Instalar
        </button>
      )}
      <button type="button" onClick={descartar} aria-label="Ahora no" title="Ahora no" className="shrink-0 rounded-lg p-1 text-muted hover:bg-line/60 hover:text-ink">
        <X size={16} aria-hidden />
      </button>
    </aside>
  );
}
