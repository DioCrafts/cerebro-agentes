// AvisoError: the error of a panel, the same everywhere. A Flash (danger) announced with role="alert": our own
// sentence saying what failed in plain words, the server's or the network's detail underneath in small print
// (only when it adds something), and «Reintentar» when the panel can try again.
//
//   <AvisoError frase="No se pudieron cargar los avisos." detalle={error} onReintentar={recargar} />
//
// A panel that keeps the error in its state stores a `Fallo` and renders <AvisoFallo fallo={fallo} />.

import { SyncIcon } from "@primer/octicons-react";
import { Button, Flash } from "@primer/react";
import type { ReactNode } from "react";

export interface AvisoErrorProps {
  /** What failed, in plain words, with our own sentence. */
  frase: string;
  /** The server's or the network's detail, shown smaller underneath. */
  detalle?: string | null;
  /** When present, a «Reintentar» button calls it. */
  onReintentar?: () => void;
  /** Extra content under the detail (for example, a note on the last valid result). */
  children?: ReactNode;
  className?: string;
}

export function AvisoError({ frase, detalle, onReintentar, children, className }: AvisoErrorProps) {
  const extra = detalle && !frase.includes(detalle.trim()) ? detalle.trim() : null;
  return (
    <Flash variant="danger" role="alert" className={className}>
      <div className="flex flex-col gap-2">
        <p className="m-0 font-semibold">{frase}</p>
        {extra && <p className="m-0 break-words text-xs">{extra}</p>}
        {children}
        {onReintentar && (
          <div>
            <Button size="small" leadingVisual={SyncIcon} onClick={onReintentar}>
              Reintentar
            </Button>
          </div>
        )}
      </div>
    </Flash>
  );
}

/** An error as a panel keeps it in its state: our sentence, the detail and how to try again. */
export interface Fallo {
  frase: string;
  detalle?: string | null;
  reintentar?: () => void;
}

/** Renders a `Fallo` held in state, or nothing. */
export function AvisoFallo({ fallo, className }: { fallo: Fallo | null; className?: string }) {
  if (!fallo) return null;
  return <AvisoError frase={fallo.frase} detalle={fallo.detalle} onReintentar={fallo.reintentar} className={className} />;
}
