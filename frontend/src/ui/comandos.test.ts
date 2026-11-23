import { describe, expect, it } from "vitest";

import { filtrarComandos, type Comando } from "./comandos";
import { BellIcon } from "./iconos";

const comando = (etiqueta: string, extra: Partial<Comando> = {}): Comando => ({
  id: etiqueta,
  grupo: "Secciones",
  etiqueta,
  icono: BellIcon,
  ejecutar: () => undefined,
  ...extra,
});

describe("filtrarComandos", () => {
  const todos = [comando("Módulos", { descripcion: "Módulos del hogar" }), comando("Simulador", { palabras: "irpf renta" }), comando("Nueva consulta", { grupo: "Acciones" })];

  it("returns everything for an empty search", () => {
    expect(filtrarComandos(todos, "  ")).toHaveLength(3);
  });

  it("ignores case and accents and needs every word", () => {
    expect(filtrarComandos(todos, "modulos HOGAR").map((c) => c.id)).toEqual(["Módulos"]);
    expect(filtrarComandos(todos, "renta").map((c) => c.id)).toEqual(["Simulador"]);
    expect(filtrarComandos(todos, "acciones nueva").map((c) => c.id)).toEqual(["Nueva consulta"]);
    expect(filtrarComandos(todos, "modulos renta")).toEqual([]);
  });
});
