// «Hay una versión nueva»: a new build of the app is installed and waits; the user decides when to reload
// (a half-written question is never lost to an automatic reload). A floating box, like GitHub's toasts.

import { aplicarActualizacion, descartarActualizacion, usePwa } from "../lib/pwa";
import { Button } from "../ui";
import { SyncIcon } from "../ui/iconos";

export function AvisoActualizacion() {
  const { actualizacion } = usePwa();
  if (!actualizacion) return null;
  return (
    <div
      role="status"
      aria-live="polite"
      className="fixed inset-x-4 bottom-[max(env(safe-area-inset-bottom),16px)] z-50 mx-auto flex max-w-md flex-wrap items-center gap-2 rounded-medium border border-border-default bg-canvas-default px-4 py-3 shadow-floating-md sm:inset-x-auto sm:right-6"
    >
      <SyncIcon className="text-fg-accent" />
      <span className="flex-1 font-semibold">Hay una versión nueva</span>
      <Button variant="invisible" onClick={descartarActualizacion}>
        Más tarde
      </Button>
      <Button variant="primary" onClick={aplicarActualizacion}>
        Actualizar
      </Button>
    </div>
  );
}
