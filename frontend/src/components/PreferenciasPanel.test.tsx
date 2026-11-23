import "../test/primerEnJsdom";

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { Decision } from "../apiMemoria";

vi.mock("../apiMemoria", async (original) => ({
  ...(await original<typeof import("../apiMemoria")>()),
  getDecisiones: vi.fn(),
  borrarDecision: vi.fn(),
  crearDecision: vi.fn(),
  editarDecision: vi.fn(),
}));

import * as api from "../apiMemoria";
import { PreferenciasPanel } from "./PreferenciasPanel";

const decision = {
  id: "d1",
  case_id: "c1",
  ambito: "plan_pensiones",
  etiqueta: "Planes de pensiones",
  modulo: "fiscal",
  tipo: "rechazo",
  razon: "necesito liquidez",
  fuente: "chat",
  fecha: "2026-11-01",
  caducidad: null,
  condiciones_de_reapertura: [],
  omisiones: 0,
  estado: "vigente",
} as Decision;

describe("PreferenciasPanel", () => {
  beforeEach(() => {
    vi.mocked(api.getDecisiones).mockResolvedValue({
      case_id: "c1",
      hoy: "2026-11-23",
      decisiones: [decision],
      catalogo: [{ ambito: "plan_pensiones", etiqueta: "Planes de pensiones" }],
      historial: [],
    });
  });

  it("forgets a decision only after confirming, and tells the shell", async () => {
    const user = userEvent.setup();
    const onChanged = vi.fn();
    vi.mocked(api.borrarDecision).mockResolvedValue(undefined as never);
    render(<PreferenciasPanel open caseId="c1" onChanged={onChanged} />);
    const vigor = await screen.findByRole("region", { name: "En vigor" });
    expect(vigor).toHaveTextContent("«necesito liquidez»");
    await user.click(within(vigor).getByRole("button", { name: "Olvidar: Planes de pensiones" }));
    await user.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Olvidar" }));
    await waitFor(() => expect(api.borrarDecision).toHaveBeenCalledWith("c1", "d1"));
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
  });

  it("adds a preference with the user's words", async () => {
    const user = userEvent.setup();
    vi.mocked(api.crearDecision).mockResolvedValue(undefined as never);
    render(<PreferenciasPanel open caseId="c1" />);
    await user.click(await screen.findByRole("button", { name: "Añadir una preferencia" }));
    const form = screen.getByRole("form", { name: "Nueva preferencia" });
    await user.type(within(form).getByRole("textbox", { name: /Con tus palabras/ }), "no quiero fondos");
    await user.click(within(form).getByRole("checkbox", { name: "Sin fecha (hasta que lo cambie)" }));
    await user.click(within(form).getByRole("button", { name: "Guardar" }));
    await waitFor(() =>
      expect(api.crearDecision).toHaveBeenCalledWith("c1", { ambito: "plan_pensiones", tipo: "rechazo", razon: "no quiero fondos", caducidad: null }),
    );
  });

  it("without a case it explains what will appear", () => {
    render(<PreferenciasPanel open caseId={null} />);
    expect(screen.getByRole("heading", { name: "Aún no tienes expediente" })).toBeInTheDocument();
    expect(api.getDecisiones).not.toHaveBeenCalled();
  });
});
