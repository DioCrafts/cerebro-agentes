import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import "../test/primerEnJsdom";

import type { EstadoCorreo } from "../apiCorreo";

vi.mock("../apiCorreo", () => ({
  getEstadoCorreo: vi.fn(),
  getResumenCorreo: vi.fn(),
  getPropuestasCorreo: vi.fn(),
  iniciarConexion: vi.fn(),
  desconectarCorreo: vi.fn(),
  decidirPropuesta: vi.fn(),
  silenciar: vi.fn(),
  sincronizarCorreo: vi.fn(),
}));
vi.mock("../lib/correo", () => ({ recordarCasoConectando: vi.fn() }));

import * as api from "../apiCorreo";
import { recordarCasoConectando } from "../lib/correo";
import { CorreoSeccion } from "./CorreoSeccion";

const estado = (extra: Partial<EstadoCorreo>): EstadoCorreo => ({
  case_id: "c1",
  configurado: true,
  activo: true,
  conectado: false,
  alcance: [],
  elementos: 0,
  propuestas_pendientes: 0,
  ventana_dias: 30,
  solo_lectura: true,
  conectada_el: null,
  cuenta: null,
  estado: null,
  ultima_sincronizacion: null,
  ultimo_error: null,
  ...extra,
});

describe("CorreoSeccion", () => {
  const assign = vi.fn();

  beforeEach(() => {
    vi.mocked(api.getResumenCorreo).mockResolvedValue({ periodo: "semana", secciones: [], otros: {}, silenciados: 0 } as never);
    vi.mocked(api.getPropuestasCorreo).mockResolvedValue([]);
    vi.spyOn(window, "location", "get").mockReturnValue({ ...window.location, assign });
  });
  afterEach(() => {
    assign.mockReset();
  });

  it("goes to Google's consent screen when the server returns it", async () => {
    const user = userEvent.setup();
    vi.mocked(api.getEstadoCorreo).mockResolvedValue(estado({}));
    vi.mocked(api.iniciarConexion).mockResolvedValue("https://accounts.google.com/o/oauth2/v2/auth?client_id=x");
    render(<CorreoSeccion caseId="c1" disabled={false} />);
    await user.click(await screen.findByRole("button", { name: "Conectar Gmail" }));
    await waitFor(() => expect(assign).toHaveBeenCalledWith("https://accounts.google.com/o/oauth2/v2/auth?client_id=x"));
    expect(recordarCasoConectando).toHaveBeenCalledWith("c1");
  });

  it("refuses any other address the server returns", async () => {
    const user = userEvent.setup();
    vi.mocked(api.getEstadoCorreo).mockResolvedValue(estado({}));
    vi.mocked(api.iniciarConexion).mockResolvedValue("https://accounts.google.com.evil.example/auth");
    render(<CorreoSeccion caseId="c1" disabled={false} />);
    await user.click(await screen.findByRole("button", { name: "Conectar Gmail" }));
    expect(await screen.findByText(/dirección de conexión inesperada/)).toBeInTheDocument();
    expect(assign).not.toHaveBeenCalled();
    expect(recordarCasoConectando).not.toHaveBeenCalled();
  });

  it("disconnects from the danger zone, erasing the data only when asked", async () => {
    const user = userEvent.setup();
    vi.mocked(api.getEstadoCorreo).mockResolvedValue(estado({ conectado: true, estado: "conectada", cuenta: "ana@example.com", elementos: 3 }));
    vi.mocked(api.desconectarCorreo).mockResolvedValue(undefined as never);
    render(<CorreoSeccion caseId="c1" disabled={false} />);
    const zona = await screen.findByRole("region", { name: "Zona de peligro" });
    expect(zona).toHaveTextContent("Desconectar Gmail");
    await user.click(screen.getByRole("button", { name: "Desconectar" }));
    const dialogo = await screen.findByRole("dialog", { name: "Desconectar Gmail" });
    expect(dialogo).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Desconectar y borrar" }));
    await waitFor(() => expect(api.desconectarCorreo).toHaveBeenCalledWith("c1", true));
    expect(await screen.findByText("Gmail desconectado y datos del correo borrados.")).toBeInTheDocument();
  });

  it("cancelling the disconnection does nothing", async () => {
    const user = userEvent.setup();
    vi.mocked(api.getEstadoCorreo).mockResolvedValue(estado({ conectado: true, estado: "conectada" }));
    render(<CorreoSeccion caseId="c1" disabled={false} />);
    await user.click(await screen.findByRole("button", { name: "Desconectar" }));
    await user.click(await screen.findByRole("button", { name: "Cancelar" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(api.desconectarCorreo).not.toHaveBeenCalled();
  });
});
