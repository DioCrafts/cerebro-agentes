import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { borrarSesion, listarSesiones } from "../apiConsejo";
import { resumen, sesion } from "../components/conversacion/consejo.fixtures";
import { useSesionConsejo } from "../shell/useSesionConsejo";
import PanelConsejo from "./consejo";
import type { ContextoPanel } from "./tipos";
import { simularNavegador } from "../components/conversacion/entornoPruebas";

simularNavegador();

vi.mock("../shell/useSesionConsejo", () => ({ useSesionConsejo: vi.fn() }));
vi.mock("../apiConsejo", async (original) => ({
  ...(await original<typeof import("../apiConsejo")>()),
  listarSesiones: vi.fn(),
  borrarSesion: vi.fn(),
  convocarConsejo: vi.fn(),
}));

const ctx = (caseId: string | null) => ({ caso: { id: caseId, facts: [], taxPeriod: null, obligations: [] } }) as unknown as ContextoPanel;

describe("PanelConsejo", () => {
  beforeEach(() => {
    vi.mocked(listarSesiones).mockReset().mockResolvedValue({ case_id: "c1", disponible: true, sesiones: [resumen()] });
    vi.mocked(borrarSesion).mockReset().mockResolvedValue(undefined as never);
    vi.mocked(useSesionConsejo).mockReturnValue({ sesion: sesion(), error: null, recargar: () => undefined });
  });

  it("explains itself before there is a case", () => {
    render(<PanelConsejo ctx={ctx(null)} open onClose={() => undefined} />);
    expect(screen.getByRole("heading", { name: "Aún no hay nada en el consejo" })).toBeInTheDocument();
  });

  it("lists the sessions with their state and opens one", async () => {
    const user = userEvent.setup();
    render(<PanelConsejo ctx={ctx("c1")} open onClose={() => undefined} />);
    const lista = await screen.findByRole("region", { name: "Sesiones del consejo" });
    expect(lista).toHaveTextContent("Dictamen listo");
    await user.click(within(lista).getByRole("button", { name: "¿Amortizo la hipoteca o invierto los ahorros?" }));
    expect(screen.getByRole("article", { name: "Consejo del hogar" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Todas las sesiones" })).toBeInTheDocument();
  });

  it("deletes a session only after confirming", async () => {
    const user = userEvent.setup();
    render(<PanelConsejo ctx={ctx("c1")} open onClose={() => undefined} />);
    await user.click(await screen.findByRole("button", { name: "¿Amortizo la hipoteca o invierto los ahorros?" }));
    await user.click(screen.getByRole("button", { name: "Borrar esta sesión" }));
    const dialogo = await screen.findByRole("alertdialog", { name: "¿Borro esta sesión del consejo?" });
    await user.click(within(dialogo).getByRole("button", { name: "Borrar" }));
    expect(borrarSesion).toHaveBeenCalledWith("c1", "s1");
  });
});
