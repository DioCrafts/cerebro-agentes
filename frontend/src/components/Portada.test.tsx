import "../test/primerEnJsdom";

import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { ModuloInfo } from "../apiModulos";
import { AREAS, estadoArea } from "../lib/areas";
import { REGISTRO } from "../paneles/registro";
import { Portada } from "./Portada";

const modulo = (clave: string, activo: boolean): ModuloInfo => ({
  clave,
  nombre: clave,
  descripcion: "",
  activo,
  obligatorio: false,
  elegido: true,
  hechos: [],
  herramientas: [],
  fuentes: [],
  capacidad: null,
  elegido_el: null,
  herramienta_de_documento: null,
  lee_documentos: false,
});

function pintar(extra: Partial<Parameters<typeof Portada>[0]> = {}) {
  const props = {
    onPick: vi.fn(),
    onAbrir: vi.fn(),
    paneles: REGISTRO,
    modulos: null,
    avisosSinLeer: 0,
    accionesPendientes: 0,
    ...extra,
  };
  render(<Portada {...props} />);
  return props;
}

const tarjeta = (nombre: string) => screen.getByRole("article", { name: nombre });

describe("Portada", () => {
  it("se presenta como mayordomo general, no como asesor fiscal", () => {
    pintar();
    expect(screen.getByRole("heading", { level: 2, name: "¿En qué te ayudo hoy?" })).toBeInTheDocument();
    expect(screen.queryByText(/duda fiscal/i)).not.toBeInTheDocument();
    for (const area of AREAS) expect(tarjeta(area.nombre)).toBeInTheDocument();
  });

  it("manda al chat la pregunta de ejemplo de un área", async () => {
    const { onPick } = pintar();
    await userEvent.click(within(tarjeta("Dinero y gastos")).getByRole("button", { name: /suscripciones pago/ }));
    expect(onPick).toHaveBeenCalledWith(expect.stringContaining("suscripciones"));
  });

  it("enlaza las herramientas del área que se ofrecen y abre su panel", async () => {
    const { onAbrir } = pintar({ paneles: REGISTRO.filter((p) => p.id !== "autonomos") });
    const impuestos = tarjeta("Impuestos y trámites");
    expect(within(impuestos).queryByRole("button", { name: "Autónomos" })).not.toBeInTheDocument();
    await userEvent.click(within(impuestos).getByRole("button", { name: "Simulador" }));
    expect(onAbrir).toHaveBeenCalledWith("simulador");
  });

  it("marca las áreas futuras como próximamente y sin ejemplos", () => {
    pintar();
    const salud = tarjeta("Salud");
    expect(within(salud).getByText("Próximamente")).toBeInTheDocument();
    expect(within(salud).queryAllByRole("button")).toHaveLength(0);
  });

  it("un área con todos sus módulos apagados lleva a activarla en vez de proponer preguntas", async () => {
    const { onAbrir } = pintar({ modulos: [modulo("luz", false), modulo("hipoteca", false), modulo("caducidades", false)] });
    const casa = tarjeta("Casa y suministros");
    expect(within(casa).getByText("Desactivada")).toBeInTheDocument();
    expect(within(casa).queryByRole("button", { name: /luz/ })).not.toBeInTheDocument();
    await userEvent.click(within(casa).getByRole("button", { name: "Activar en Áreas" }));
    expect(onAbrir).toHaveBeenCalledWith("modulos");
  });

  it("enseña lo pendiente solo cuando hay algo", async () => {
    const { onAbrir } = pintar({ avisosSinLeer: 2, accionesPendientes: 1 });
    const pendiente = screen.getByRole("region", { name: "Pendiente para ti" });
    expect(within(pendiente).getByText("Tienes 2 avisos sin leer")).toBeInTheDocument();
    await userEvent.click(within(pendiente).getByRole("button", { name: "Revisar" }));
    expect(onAbrir).toHaveBeenCalledWith("modulos");
  });

  it("sin nada pendiente no hay sección de pendientes", () => {
    pintar();
    expect(screen.queryByRole("region", { name: "Pendiente para ti" })).not.toBeInTheDocument();
  });
});

describe("estadoArea", () => {
  const casa = AREAS.find((a) => a.id === "casa")!;

  it("cuenta como lista mientras no se conocen los módulos", () => {
    expect(estadoArea(casa, null)).toBe("lista");
  });

  it("basta un módulo encendido para que esté lista", () => {
    expect(estadoArea(casa, [modulo("luz", false), modulo("hipoteca", true)])).toBe("lista");
  });

  it("un módulo que el backend no lista no la apaga", () => {
    expect(estadoArea(casa, [modulo("fiscal", true)])).toBe("lista");
  });

  it("cada panel que nombra un área existe en el registro", () => {
    const ids = new Set(REGISTRO.map((p) => p.id));
    for (const area of AREAS) for (const id of area.paneles ?? []) expect(ids.has(id)).toBe(true);
  });
});
