// The state of a council session as a StateLabel, the same in the chat card and in the «Consejo» panel's list.

import { ESTADO_LABEL, EN_MARCHA, type EstadoSesion } from "../../apiConsejo";
import { Spinner } from "../../ui";
import { AlertIcon, CheckIcon, ClockIcon, SkipIcon } from "../../ui/iconos";
import { EtiquetaEstado, type TonoEstado } from "./EtiquetaEstado";

function tono(estado: EstadoSesion): TonoEstado {
  if (estado === "terminada") return "exito";
  if (estado === "fallida") return "peligro";
  if (estado === "pendiente_confirmacion") return "atencion";
  if (EN_MARCHA.has(estado)) return "acento";
  return "neutro";
}

export function EstadoSesionLabel({ estado }: { estado: EstadoSesion }) {
  const visual = EN_MARCHA.has(estado) ? (
    <Spinner size="small" srText={null} />
  ) : estado === "terminada" ? (
    <CheckIcon size={12} />
  ) : estado === "fallida" ? (
    <AlertIcon size={12} />
  ) : estado === "pendiente_confirmacion" ? (
    <ClockIcon size={12} />
  ) : (
    <SkipIcon size={12} />
  );
  return (
    <EtiquetaEstado tono={tono(estado)} visual={visual}>
      {ESTADO_LABEL[estado]}
    </EtiquetaEstado>
  );
}
