import "../test/primerEnJsdom";

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { Fact } from "../api";
import type { AccionPendiente, ModuloInfo } from "../apiModulos";

vi.mock("../apiModulos", async (original) => ({
  ...(await original<typeof import("../apiModulos")>()),
  getPropuestas: vi.fn(),
  getCapacidades: vi.fn(),
  setCapacidad: vi.fn(),
}));

import * as api from "../apiModulos";
import { ModulosPanel } from "./ModulosPanel";

const modulo = (extra: Partial<ModuloInfo>): ModuloInfo => ({
  clave: "luz",
  nombre: "Luz",
  descripcion: "Tu factura de la luz",
  activo: true,
  obligatorio: false,
  elegido: true,
  hechos: [{ nombre: "luz_potencia", tipo: "number", etiqueta: "Potencia contratada", descripcion: "En kW", sensible: false }],
  herramientas: [],
  fuentes: [],
  ...extra,
});

const dato: Fact = { fact_id: "f1", name: "luz_potencia", value: 4.6, value_type: "number", source: "user", confidence: 1, updated_at: "2026-11-01" };

const accion: AccionPendiente = {
  id: "a1",
  case_id: "c1",
  modulo: "luz",
  herramienta: "cambiar_tarifa",
  argumentos: {},
  resumen: "Cambiar a la tarifa PVPC",
  estado: "pendiente",
  created_at: new Date().toISOString(),
  es_error: false,
};

function montar(extra: Partial<Parameters<typeof ModulosPanel>[0]> = {}) {
  const props = {
    open: true,
    modulos: { configurable: true, modulos: [modulo({}), modulo({ clave: "fiscal", nombre: "Impuestos", obligatorio: true, hechos: [] })] },
    loading: false,
    error: null,
    onToggle: vi.fn().mockResolvedValue(true),
    caseId: "c1",
    facts: [dato],
    onCaseUpdated: vi.fn(),
    acciones: [] as AccionPendiente[],
    onDecide: vi.fn().mockResolvedValue(undefined),
    disabled: false,
    ...extra,
  };
  render(<ModulosPanel {...props} />);
  return props;
}

describe("ModulosPanel", () => {
  beforeEach(() => {
    vi.mocked(api.getPropuestas).mockResolvedValue([]);
    vi.mocked(api.getCapacidades).mockResolvedValue({ configurable: true, capacidades: [] });
  });

  it("lists the modules with their switch and opens one from the side navigation", async () => {
    const user = userEvent.setup();
    montar();
    const nav = screen.getByRole("navigation", { name: "Secciones de los módulos" });
    expect(within(nav).getByRole("link", { name: /General/ })).toHaveAttribute("aria-current", "page");
    const lista = screen.getByRole("region", { name: "Módulos" });
    expect(within(lista).getByRole("button", { name: "Luz" })).toHaveAttribute("aria-pressed", "true");
    expect(within(lista).getByText("Siempre activo")).toBeInTheDocument();

    await user.click(within(nav).getByRole("link", { name: "Luz" }));
    expect(screen.getByRole("heading", { level: 3, name: "Luz" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Tus datos de Luz" })).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Potencia contratada" })).toHaveValue("4.6");
  });

  it("switching a module off asks whether to keep or erase its data", async () => {
    const user = userEvent.setup();
    const props = montar();
    await user.click(within(screen.getByRole("region", { name: "Módulos" })).getByRole("button", { name: "Luz" }));
    expect(await screen.findByRole("dialog", { name: "Desactivar Luz" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Desactivar y borrar sus datos" }));
    await waitFor(() => expect(props.onToggle).toHaveBeenCalledWith("luz", false, true));
  });

  it("switching a module on needs no confirmation", async () => {
    const user = userEvent.setup();
    const props = montar({ modulos: { configurable: true, modulos: [modulo({ activo: false })] }, facts: [] });
    await user.click(within(screen.getByRole("region", { name: "Módulos" })).getByRole("button", { name: "Luz" }));
    expect(props.onToggle).toHaveBeenCalledWith("luz", true, false);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("a pending action runs only after its confirmation dialog", async () => {
    const user = userEvent.setup();
    const props = montar({ acciones: [accion] });
    const pendientes = screen.getByRole("region", { name: "Pendiente de tu confirmación" });
    await user.click(within(pendientes).getByRole("button", { name: "Confirmar" }));
    const dialogo = await screen.findByRole("alertdialog", { name: "¿Ejecutar esta acción ahora?" });
    expect(props.onDecide).not.toHaveBeenCalled();
    await user.click(within(dialogo).getByRole("button", { name: "Sí, ejecutarla ahora" }));
    await waitFor(() => expect(props.onDecide).toHaveBeenCalledWith("a1", true));
  });

  it("the module's danger zone deactivates it after confirming", async () => {
    const user = userEvent.setup();
    const props = montar();
    await user.click(within(screen.getByRole("navigation", { name: "Secciones de los módulos" })).getByRole("link", { name: "Luz" }));
    const zona = screen.getByRole("region", { name: "Zona de peligro" });
    await user.click(within(zona).getByRole("button", { name: "Desactivar" }));
    const dialogo = await screen.findByRole("alertdialog", { name: "¿Desactivar Luz?" });
    await user.click(within(dialogo).getByRole("button", { name: "Cancelar" }));
    expect(props.onToggle).not.toHaveBeenCalled();
  });
});
