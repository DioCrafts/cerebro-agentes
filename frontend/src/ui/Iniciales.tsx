// An avatar with initials (there are no profile pictures): a circle on the accent tint.

import { clsx } from "clsx";

import estilos from "./Iniciales.module.css";

export function Iniciales({ texto, tamano = 32, className }: { texto: string; tamano?: number; className?: string }) {
  const letras = texto.trim().slice(0, 2).toUpperCase() || "?";
  return (
    <span className={clsx(estilos.iniciales, className)} style={{ width: tamano, height: tamano, fontSize: Math.max(10, Math.round(tamano * 0.4)) }} aria-hidden>
      {letras}
    </span>
  );
}
