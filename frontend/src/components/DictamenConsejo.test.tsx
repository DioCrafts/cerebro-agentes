import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { AdvisorNotification } from "../api";
import { listarSesiones } from "../apiConsejo";
import { useSesionConsejo } from "../shell/useSesionConsejo";
import { resumen, sesion } from "./conversacion/consejo.fixtures";
import { AvisoConsejo, DictamenConsejo } from "./DictamenConsejo";
import { simularNavegador } from "./conversacion/entornoPruebas";

simularNavegador();

vi.mock("../shell/useSesionConsejo", () => ({ useSesionConsejo: vi.fn() }));
vi.mock("../apiConsejo", async (original) => ({ ...(await original<typeof import("../apiConsejo")>()), listarSesiones: vi.fn() }));

describe("DictamenConsejo", () => {
  it("leads with the recommendation and the council's confidence", () => {
    render(<DictamenConsejo sesion={sesion()} />);
    const recomendacion = screen.getByRole("region", { name: "Recomendación del consejo" });
    expect(recomendacion).toHaveTextContent("Amortizar la hipoteca");
    expect(recomendacion).toHaveTextContent("72 %");
    expect(screen.getByRole("article", { name: "Amortizar la hipoteca" })).toHaveTextContent("Recomendada");
    expect(screen.getByRole("region", { name: "El argumento de la minoría" })).toHaveTextContent("Invertir rinde más");
  });

  it("keeps the deliberation anonymous until the user asks who was who", async () => {
    const user = userEvent.setup();
    render(<DictamenConsejo sesion={sesion()} />);
    await user.click(screen.getByText("Cómo deliberó el consejo"));
    const propuestaA = screen.getByRole("article", { name: "Propuesta A" });
    expect(propuestaA).not.toHaveTextContent("Prudente");
    const revelar = screen.getByRole("button", { name: "Revelar quién era quién" });
    await user.click(revelar);
    expect(screen.getByRole("button", { name: "Ocultar quién era quién" })).toHaveAttribute("aria-pressed", "true");
    expect(propuestaA).toHaveTextContent("Prudente");
    expect(screen.getByRole("list", { name: "Quién era quién" })).toHaveTextContent("proveedor-a · modelo-a");
  });

  it("shows the phases while the council is working", () => {
    render(<DictamenConsejo sesion={sesion({ estado: "deliberando", dictamen: null, progreso: [{ tipo: "fase", fase: "instruccion", estado: "fin", miembro_id: null, en: "" }] })} />);
    const fases = screen.getByRole("list", { name: "Fases del consejo" });
    const [expediente, propuestas] = within(fases).getAllByRole("listitem") as [HTMLElement, HTMLElement];
    expect(expediente).toHaveTextContent("Expediente: hecho");
    expect(propuestas).toHaveTextContent("Propuestas: pendiente");
  });
});

describe("AvisoConsejo", () => {
  beforeEach(() => {
    vi.mocked(listarSesiones).mockReset().mockResolvedValue({ case_id: "c1", disponible: true, sesiones: [resumen()] });
    vi.mocked(useSesionConsejo).mockImplementation((_caso, id) => ({ sesion: id ? sesion() : null, error: null, recargar: () => undefined }));
  });

  it("opens the ruling on demand and marks the notice as read", async () => {
    const user = userEvent.setup();
    const onMarkRead = vi.fn();
    const n = { id: "n9", case_id: "c1", kind: "modulo:consejo", severity: "info", title: "Dictamen del consejo listo", body: "Ya está.", amount: null, due_date: null, actions: [], sources: [], created_at: new Date().toISOString(), read_at: null } as unknown as AdvisorNotification;
    render(<AvisoConsejo notification={n} caseId="c1" onMarkRead={onMarkRead} />);
    const aviso = screen.getByRole("article", { name: "Dictamen del consejo listo" });
    expect(aviso).toHaveTextContent("Nuevo");
    await user.click(within(aviso).getByRole("button", { name: "Ver el dictamen" }));
    expect(onMarkRead).toHaveBeenCalledWith("n9");
    expect(await screen.findByRole("region", { name: "Recomendación del consejo" })).toBeInTheDocument();
    expect(within(aviso).getByRole("button", { name: "Ocultar el dictamen" })).toHaveAttribute("aria-expanded", "true");
  });
});
