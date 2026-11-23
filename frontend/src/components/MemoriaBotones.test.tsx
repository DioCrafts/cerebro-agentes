import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { crearDecision, responderAviso, type DecisionResponse } from "../apiMemoria";
import { MemoriaBotones } from "./MemoriaBotones";
import { simularNavegador } from "./conversacion/entornoPruebas";

simularNavegador();

vi.mock("../apiMemoria", () => ({ responderAviso: vi.fn(), crearDecision: vi.fn() }));

const resp = { decision: {}, avisos_retirados: 1, plan_recalculado: false } as unknown as DecisionResponse;

describe("MemoriaBotones", () => {
  beforeEach(() => {
    vi.mocked(responderAviso).mockReset().mockResolvedValue(resp);
    vi.mocked(crearDecision).mockReset().mockResolvedValue(resp);
  });

  it("answers a notice from the menu, with the reason, only on «Guardar»", async () => {
    const user = userEvent.setup();
    const onDone = vi.fn();
    render(<MemoriaBotones caseId="c1" notificationId="n1" onDone={onDone} />);
    await user.click(screen.getByRole("button", { name: "Tu respuesta" }));
    await user.click(screen.getByRole("menuitemradio", { name: "No me interesa" }));
    expect(responderAviso).not.toHaveBeenCalled();
    await user.type(screen.getByRole("textbox", { name: "¿Por qué? (opcional, con tus palabras)" }), "tengo permanencia");
    await user.click(screen.getByRole("button", { name: "Guardar" }));
    expect(responderAviso).toHaveBeenCalledWith("c1", "n1", { boton: "no_me_interesa", razon: "tengo permanencia", hasta: undefined });
    expect(onDone).toHaveBeenCalledWith(resp);
  });

  it("offers no «remind me later» on a lever of the plan", async () => {
    const user = userEvent.setup();
    render(<MemoriaBotones caseId="c1" palanca={{ id: "p1", titulo: "Plan de pensiones" }} onDone={() => undefined} />);
    await user.click(screen.getByRole("button", { name: "Tu respuesta" }));
    expect(screen.getByRole("menuitemradio", { name: "Ya lo hice" })).toBeInTheDocument();
    expect(screen.queryByRole("menuitemradio", { name: "Recuérdamelo más tarde" })).not.toBeInTheDocument();
  });
});
