import "../test/primerEnJsdom";

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { Recuerdo, RecuerdosResponse } from "../apiRecuerdos";

vi.mock("../apiRecuerdos", async (original) => ({
  ...(await original<typeof import("../apiRecuerdos")>()),
  getRecuerdos: vi.fn(),
  getConversaciones: vi.fn(),
  cambiarInterruptor: vi.fn(),
  olvidarRecuerdo: vi.fn(),
  olvidarTodo: vi.fn(),
  borrarConversacion: vi.fn(),
  corregirRecuerdo: vi.fn(),
}));

import * as api from "../apiRecuerdos";
import { RecuerdosPanel } from "./RecuerdosPanel";

const fuente = { conversacion_id: "k1", cita: "tengo hipoteca a tipo fijo", fecha: "2026-11-12", titulo_conversacion: "Hipoteca" };

const recuerdo: Recuerdo = {
  id: "r1",
  tipo: "dato",
  texto: "Tiene una hipoteca a tipo fijo",
  origen: fuente,
  fuentes: [fuente],
  corregido: false,
  sensible: false,
  estado: "activo",
  sustituido_por: null,
  creado: "2026-11-12",
  actualizado: "2026-11-12",
  usos: 2,
  ultimo_uso: null,
};

const respuesta = (extra: Partial<RecuerdosResponse> = {}): RecuerdosResponse => ({
  disponible: true,
  activa: true,
  sin_cuenta: false,
  retencion_dias: 30,
  explicacion: "Guardo lo importante.",
  recuerdos: [recuerdo],
  historial: [],
  conversaciones: 1,
  ...extra,
});

describe("RecuerdosPanel", () => {
  beforeEach(() => {
    vi.mocked(api.getRecuerdos).mockResolvedValue(respuesta());
    vi.mocked(api.getConversaciones).mockResolvedValue({
      retencion_dias: 30,
      conversaciones: [{ id: "k1", titulo: "Hipoteca", actualizada: "2026-11-12", resumen: "Hablamos de la hipoteca.", recuerdos: 1, retenidos: 4 }],
    } as never);
  });

  it("groups what it remembers and pauses the memory with its switch", async () => {
    const user = userEvent.setup();
    vi.mocked(api.cambiarInterruptor).mockResolvedValue({ disponible: true, activa: false, explicacion: "" });
    render(<RecuerdosPanel open caseId="c1" />);
    const grupo = await screen.findByRole("region", { name: "Datos que me has contado" });
    expect(grupo).toHaveTextContent("Tiene una hipoteca a tipo fijo");
    await user.click(screen.getByRole("button", { name: "Memoria de conversaciones" }));
    await waitFor(() => expect(api.cambiarInterruptor).toHaveBeenCalledWith("c1", false));
  });

  it("shows the earlier conversations in the other view", async () => {
    const user = userEvent.setup();
    render(<RecuerdosPanel open caseId="c1" />);
    await user.click(await screen.findByRole("button", { name: /Conversaciones \(1\)/ }));
    expect(screen.getByRole("region", { name: "Conversaciones anteriores" })).toHaveTextContent("Hablamos de la hipoteca.");
  });

  it("«Olvidar todo» is in the danger zone and asks first", async () => {
    const user = userEvent.setup();
    vi.mocked(api.olvidarTodo).mockResolvedValue({ recuerdos: 1, conversaciones: 1, mensajes: 4 });
    render(<RecuerdosPanel open caseId="c1" />);
    const zona = await screen.findByRole("region", { name: "Zona de peligro" });

    await user.click(within(zona).getByRole("button", { name: "Olvidar todo" }));
    await user.click(within(await screen.findByRole("alertdialog", { name: "¿Olvido todo?" })).getByRole("button", { name: "Cancelar" }));
    expect(api.olvidarTodo).not.toHaveBeenCalled();

    await user.click(within(zona).getByRole("button", { name: "Olvidar todo" }));
    await user.click(within(await screen.findByRole("alertdialog", { name: "¿Olvido todo?" })).getByRole("button", { name: "Olvidar todo" }));
    await waitFor(() => expect(api.olvidarTodo).toHaveBeenCalledWith("c1"));
  });

  it("without an account it says what will appear", async () => {
    vi.mocked(api.getRecuerdos).mockResolvedValue(respuesta({ sin_cuenta: true, recuerdos: [] }));
    render(<RecuerdosPanel open caseId={null} />);
    expect(await screen.findByRole("heading", { name: "Aún no hay nada que recordar" })).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Zona de peligro" })).not.toBeInTheDocument();
  });
});
