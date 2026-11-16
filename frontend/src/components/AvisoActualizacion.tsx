// «Hay una versión nueva»: a new build of the app is installed and waits; the user decides when to reload
// (a half-written question is never lost to an automatic reload).

import { RefreshCw } from "lucide-react";

import { aplicarActualizacion, descartarActualizacion, usePwa } from "../lib/pwa";

export function AvisoActualizacion() {
  const { actualizacion } = usePwa();
  if (!actualizacion) return null;
  return (
    <div
      role="status"
      aria-live="polite"
      className="fixed inset-x-3 bottom-[max(env(safe-area-inset-bottom),12px)] z-50 mx-auto flex max-w-md flex-wrap items-center gap-2 rounded-xl border border-line bg-surface px-4 py-3 text-[13.5px] text-ink shadow-pop sm:inset-x-auto sm:right-6"
    >
      <RefreshCw size={16} aria-hidden className="text-primary-dark" />
      <span className="flex-1 font-semibold">Hay una versión nueva</span>
      <button
        type="button"
        onClick={descartarActualizacion}
        className="rounded-lg px-2.5 py-1.5 text-[13px] font-semibold text-muted hover:bg-line/60 hover:text-ink"
      >
        Más tarde
      </button>
      <button
        type="button"
        onClick={aplicarActualizacion}
        className="rounded-lg bg-primary px-3 py-1.5 text-[13px] font-semibold text-white hover:bg-primary-hover"
      >
        Actualizar
      </button>
    </div>
  );
}
