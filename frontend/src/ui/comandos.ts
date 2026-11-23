// The commands of the palette (PaletaComandos) and how a search finds them: every word must appear in the
// label, description, synonyms or group, ignoring case and accents.

import type { Icon } from "@primer/octicons-react";

export interface Comando {
  id: string;
  grupo: string;
  etiqueta: string;
  descripcion?: string;
  icono: Icon;
  /** Extra words that find it (synonyms). */
  palabras?: string;
  ejecutar: () => void;
}

function normalizar(texto: string): string {
  return texto
    .normalize("NFD")
    .replace(/\p{Diacritic}/gu, "")
    .toLowerCase();
}

export function filtrarComandos(comandos: Comando[], consulta: string): Comando[] {
  const partes = normalizar(consulta).split(/\s+/).filter(Boolean);
  if (partes.length === 0) return comandos;
  return comandos.filter((c) => {
    const texto = normalizar(`${c.etiqueta} ${c.descripcion ?? ""} ${c.palabras ?? ""} ${c.grupo}`);
    return partes.every((p) => texto.includes(p));
  });
}

