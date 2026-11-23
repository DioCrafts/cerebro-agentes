import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { confirmarSesion, convocarConsejo, detectarDecision, listarSesiones } from "../apiConsejo";
import { useSesionConsejo } from "../shell/useSesionConsejo";
import { resumen, sesion } from "./conversacion/consejo.fixtures";
import { ConsejoEnHilo, ConvocarConsejo } from "./ConvocarConsejo";
import { simularNavegador } from "./conversacion/entornoPruebas";

simularNavegador();

vi.mock("../shell/useSesionConsejo", () => ({ useSesionConsejo: vi.fn() }));
vi.mock("../apiConsejo", async (original) => ({
  ...(await original<typeof import("../apiConsejo")>()),
  confirmarSesion: vi.fn(),
  rechazarSesion: vi.fn(),
  cancelarSesion: vi.fn(),
  convocarConsejo: vi.fn(),
  detectarDecision: vi.fn(),
  listarSesiones: vi.fn(),
}));

describe("ConvocarConsejo", () => {
  const recargar = vi.fn();
  beforeEach(() => {
    vi.mocked(confirmarSesion).mockReset().mockResolvedValue(sesion({ estado: "en_cola" }));
    vi.mocked(useSesionConsejo).mockReturnValue({ sesion: sesion({ estado: "pendiente_confirmacion", dictamen: null }), error: null, recargar });
  });

  it("asks before spending: the estimate, then «Confirmar»", async () => {
    const user = userEvent.setup();
    const onCambio = vi.fn();
    render(<ConvocarConsejo caseId="c1" sesionId="s1" onCambio={onCambio} />);
    const tarjeta = screen.getByRole("article", { name: "Consejo del hogar" });
    expect(tarjeta).toHaveTextContent("¿Lo consultamos con el consejo?");
    expect(tarjeta).toHaveTextContent("Esperando tu confirmación");
    expect(tarjeta).toHaveTextContent("Coste estimado");
    await user.click(screen.getByRole("button", { name: "Confirmar" }));
    expect(confirmarSesion).toHaveBeenCalledWith("c1", "s1");
    expect(recargar).toHaveBeenCalled();
    expect(onCambio).toHaveBeenCalled();
  });

  it("folds the finished ruling behind «Ver el dictamen»", async () => {
    const user = userEvent.setup();
    vi.mocked(useSesionConsejo).mockReturnValue({ sesion: sesion(), error: null, recargar });
    render(<ConvocarConsejo caseId="c1" sesionId="s1" />);
    expect(screen.queryByRole("region", { name: "Recomendación del consejo" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Ver el dictamen" }));
    expect(screen.getByRole("region", { name: "Recomendación del consejo" })).toBeInTheDocument();
  });
});

describe("ConsejoEnHilo", () => {
  beforeEach(() => {
    sessionStorage.clear();
    vi.mocked(useSesionConsejo).mockReturnValue({ sesion: null, error: null, recargar: () => undefined });
    vi.mocked(listarSesiones).mockReset().mockResolvedValue({ case_id: "c1", disponible: true, sesiones: [] });
    vi.mocked(detectarDecision)
      .mockReset()
      .mockResolvedValue({ dificil: true, motivos: ["Importe grande"], opciones_detectadas: ["amortizar", "invertir"], puntuacion: 0.9, pedido_por_usuario: false, factual: false });
    vi.mocked(convocarConsejo).mockReset().mockResolvedValue(sesion({ id: "s2", estado: "pendiente_confirmacion" }));
  });

  it("offers the council on a hard decision and convenes it on request", async () => {
    const user = userEvent.setup();
    render(<ConsejoEnHilo caseId="c1" turnos={1} ultimaPregunta="¿Amortizo o invierto?" ocupado={false} />);
    const oferta = await screen.findByRole("region", { name: "Propuesta de consultar al consejo" });
    expect(oferta).toHaveTextContent("Importe grande");
    vi.mocked(listarSesiones).mockResolvedValue({ case_id: "c1", disponible: true, sesiones: [resumen({ id: "s2", estado: "pendiente_confirmacion" })] });
    await user.click(screen.getByRole("button", { name: "Consultarlo con el consejo" }));
    expect(convocarConsejo).toHaveBeenCalledWith("c1", { pregunta: "¿Amortizo o invierto?", opciones: ["amortizar", "invertir"] });
  });

  it("remembers that the user declined the offer", async () => {
    const user = userEvent.setup();
    render(<ConsejoEnHilo caseId="c1" turnos={1} ultimaPregunta="¿Amortizo o invierto?" ocupado={false} />);
    await user.click(await screen.findByRole("button", { name: "No hace falta" }));
    expect(screen.queryByRole("region", { name: "Propuesta de consultar al consejo" })).not.toBeInTheDocument();
    expect(sessionStorage.getItem("consejo_sugerencias_descartadas_v1")).toContain("¿Amortizo o invierto?");
  });
});
