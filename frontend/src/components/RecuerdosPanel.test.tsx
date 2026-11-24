import "../test/primerEnJsdom";

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "../api";
import type { CorreccionHecha, Recuerdo, RecuerdosResponse } from "../apiRecuerdos";

vi.mock("../apiRecuerdos", async (original) => ({
  ...(await original<typeof import("../apiRecuerdos")>()),
  getRecuerdos: vi.fn(),
  getConversaciones: vi.fn(),
  getConversacion: vi.fn(),
  cambiarInterruptor: vi.fn(),
  olvidarRecuerdo: vi.fn(),
  olvidarTodo: vi.fn(),
  borrarConversacion: vi.fn(),
  corregirRecuerdo: vi.fn(),
}));

import * as api from "../apiRecuerdos";
import { RecuerdosPanel } from "./RecuerdosPanel";

const recuerdo: Recuerdo = {
  id: "r1",
  tipo: "hecho",
  texto: "Tiene una hipoteca a tipo fijo",
  conversacion_id: "k1",
  titulo_conversacion: "Hipoteca",
  fecha: "2026-11-12T10:00:00Z",
  corregido: false,
};

const vivencia: Recuerdo = {
  id: "r2",
  tipo: "vivencia",
  texto: "Se mudó a Valencia en octubre",
  conversacion_id: "k1",
  titulo_conversacion: "Hipoteca",
  fecha: "2026-11-12T10:05:00Z",
  corregido: false,
};

const respuesta = (extra: Partial<RecuerdosResponse> = {}): RecuerdosResponse => ({
  disponible: true,
  activa: true,
  sin_cuenta: false,
  explicacion: "Guardo lo que me escribes.",
  recuerdos: [recuerdo, vivencia],
  conversaciones: 1,
  ...extra,
});

describe("RecuerdosPanel", () => {
  beforeEach(() => {
    vi.mocked(api.getRecuerdos).mockReset().mockResolvedValue(respuesta());
    vi.mocked(api.getConversaciones).mockReset().mockResolvedValue({
      conversaciones: [{ id: "k1", titulo: "Hipoteca", creada: "2026-11-12T10:00:00Z", actualizada: "2026-11-12T10:05:00Z", mensajes: 2 }],
    });
    vi.mocked(api.corregirRecuerdo).mockReset();
    vi.mocked(api.olvidarRecuerdo).mockReset();
  });

  it("groups what it remembers by kind, with its conversation, and pauses the memory with its switch", async () => {
    const user = userEvent.setup();
    vi.mocked(api.cambiarInterruptor).mockResolvedValue({ disponible: true, activa: false, explicacion: "" });
    render(<RecuerdosPanel open caseId="c1" />);
    const hechos = await screen.findByRole("region", { name: "Lo que me has contado" });
    expect(hechos).toHaveTextContent("Tiene una hipoteca a tipo fijo");
    expect(hechos).toHaveTextContent(/Me lo dijiste el .+ en «Hipoteca»/);
    expect(screen.getByRole("region", { name: "Lo que te ha pasado" })).toHaveTextContent("Se mudó a Valencia en octubre");

    await user.click(screen.getByRole("button", { name: "Memoria de conversaciones" }));
    await waitFor(() => expect(api.cambiarInterruptor).toHaveBeenCalledWith("c1", false));
  });

  it("corrects an item with the user's words, shows it is working and reads the list again", async () => {
    const user = userEvent.setup();
    let terminar: (v: CorreccionHecha) => void = () => undefined;
    vi.mocked(api.corregirRecuerdo).mockReturnValue(new Promise((resolve) => (terminar = resolve)));
    render(<RecuerdosPanel open caseId="c1" />);

    await user.click(await screen.findByRole("button", { name: "Corregir: Tiene una hipoteca a tipo fijo" }));
    const form = screen.getByRole("form", { name: "Corregir: Tiene una hipoteca a tipo fijo" });
    const campo = within(form).getByRole("textbox");
    await user.clear(campo);
    await user.type(campo, "La hipoteca es a tipo variable");
    await user.click(within(form).getByRole("button", { name: "Guardar" }));

    expect(api.corregirRecuerdo).toHaveBeenCalledWith("c1", "r1", { texto: "La hipoteca es a tipo variable" });
    expect(within(form).getByRole("status")).toHaveTextContent("tarda unos segundos");
    expect(screen.getByRole("button", { name: "Olvidar: Se mudó a Valencia en octubre" })).toBeDisabled();

    terminar({ recuerdos: [{ ...recuerdo, id: "r3", texto: "Tiene una hipoteca a tipo variable", corregido: true }] });
    expect(await screen.findByText(/ahora recuerdo: «Tiene una hipoteca a tipo variable»/)).toBeInTheDocument();
    await waitFor(() => expect(api.getRecuerdos).toHaveBeenCalledTimes(2));
  });

  it("forgetting one item warns that the whole message it came from goes", async () => {
    const user = userEvent.setup();
    vi.mocked(api.olvidarRecuerdo).mockResolvedValue(null);
    render(<RecuerdosPanel open caseId="c1" />);

    await user.click(await screen.findByRole("button", { name: "Olvidar: Tiene una hipoteca a tipo fijo" }));
    const dialogo = await screen.findByRole("alertdialog", { name: "¿Olvido esto?" });
    expect(dialogo).toHaveTextContent("Borro el mensaje tuyo del que lo saqué y todo lo que aprendí de él");
    await user.click(within(dialogo).getByRole("button", { name: "Olvidar" }));
    await waitFor(() => expect(api.olvidarRecuerdo).toHaveBeenCalledWith("c1", "r1"));
  });

  it("shows the earlier conversations and the user's kept messages", async () => {
    const user = userEvent.setup();
    vi.mocked(api.getConversacion).mockResolvedValue({
      conversacion: { id: "k1", titulo: "Hipoteca", creada: null, actualizada: null, mensajes: 1 },
      mensajes: [{ texto: "Tengo hipoteca a tipo fijo", fecha: "2026-11-12T10:00:00Z" }],
      recuerdos: [recuerdo],
    });
    render(<RecuerdosPanel open caseId="c1" />);
    await user.click(await screen.findByRole("button", { name: /Conversaciones \(1\)/ }));
    const region = screen.getByRole("region", { name: "Conversaciones anteriores" });
    expect(region).toHaveTextContent("guardo 2 mensajes tuyos");

    await user.click(within(region).getByRole("button", { name: "Ver mensajes" }));
    expect(await within(region).findByRole("list", { name: "Tus mensajes" })).toHaveTextContent("Tengo hipoteca a tipo fijo");
    expect(api.getConversacion).toHaveBeenCalledWith("c1", "k1", expect.any(AbortSignal));
  });

  it("shows the server's message when the memory does not answer", async () => {
    vi.mocked(api.getRecuerdos).mockRejectedValue(
      new ApiError("La memoria no responde ahora mismo (está arrancando o se ha parado). Prueba en un minuto.", 503),
    );
    render(<RecuerdosPanel open caseId="c1" />);
    const alerta = await screen.findByRole("alert");
    expect(alerta).toHaveTextContent("No he podido cargar lo que recuerdo.");
    expect(alerta).toHaveTextContent("La memoria no responde ahora mismo");
    expect(within(alerta).getByRole("button", { name: "Reintentar" })).toBeInTheDocument();
  });

  it("«Olvidar todo» is in the danger zone and asks first", async () => {
    const user = userEvent.setup();
    vi.mocked(api.olvidarTodo).mockResolvedValue({ recuerdos: 2, conversaciones: 1 });
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
