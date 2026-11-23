import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { getRepaso, type AdvisorNotification, type Repaso } from "../api";
import { confirmarAccion } from "../apiModulos";
import { RepasoCard } from "./RepasoCard";
import { simularNavegador } from "./conversacion/entornoPruebas";

simularNavegador();

vi.mock("../api", async (original) => ({ ...(await original<typeof import("../api")>()), getRepaso: vi.fn() }));
vi.mock("../apiModulos", async (original) => ({ ...(await original<typeof import("../apiModulos")>()), confirmarAccion: vi.fn(), rechazarAccion: vi.fn() }));

const aviso = {
  id: "n1",
  case_id: "c1",
  kind: "resumen_semanal",
  severity: "info",
  title: "Tu semana",
  body: "Texto del aviso.",
  amount: null,
  due_date: null,
  actions: [],
  sources: [],
  created_at: new Date().toISOString(),
  read_at: null,
} as unknown as AdvisorNotification;

const repaso: Repaso = {
  case_id: "c1",
  notification_id: "n1",
  periodo: "2026-W47",
  frecuencia: "semanal",
  origen: "modelo",
  titular: "Dos cosas esta semana",
  restantes: 0,
  actualizado: "",
  puntos: [
    {
      id: "p1",
      rango: 1,
      titulo: "Cambia de tarifa de luz",
      texto: "Pagas de más.",
      por_que: "Tu consumo es nocturno.",
      motivos: ["ahorro de 120 €/año"],
      urgente: true,
      gravedad: "urgente",
      vence: null,
      ambitos: [],
      fuentes: [{ tipo: "aviso", id: "a1", nombre_modulo: "Energía", titulo: "Factura de octubre" }],
      propuestas: [{ id: "pr1", tipo: "accion", ref: "acc_1", parametros: {}, etiqueta: "Cambiar ahora", resumen: "Cambiar a la tarifa nocturna", estado: "pendiente" }],
      origen: "modelo",
    },
  ],
};

const props = { notification: aviso, caseId: "c1", onMarkRead: vi.fn(), onRefresh: vi.fn(), onAsk: vi.fn() };

describe("RepasoCard", () => {
  beforeEach(() => {
    vi.mocked(getRepaso).mockReset().mockResolvedValue(repaso);
    vi.mocked(confirmarAccion).mockReset().mockResolvedValue(undefined as never);
  });

  it("lists the points in order, with where each comes from and why", async () => {
    const user = userEvent.setup();
    render(<RepasoCard {...props} />);
    const puntos = await screen.findByRole("list", { name: "Lo que importa, de más a menos" });
    const punto = within(puntos).getByText("Cambia de tarifa de luz").closest("li") as HTMLElement;
    expect(punto).toHaveTextContent("Cambia de tarifa de luz");
    expect(punto).toHaveTextContent("Urgente");
    expect(within(punto).getByRole("list", { name: "De dónde sale" })).toHaveTextContent("Energía");
    await user.click(within(punto).getByText("¿Por qué?"));
    expect(within(punto).getByText("Tu consumo es nocturno.")).toBeVisible();
  });

  it("asks again before an action with effects outside the app", async () => {
    const user = userEvent.setup();
    render(<RepasoCard {...props} />);
    const grupo = await screen.findByRole("group", { name: "Responder a la propuesta: Cambiar ahora" });
    await user.click(within(grupo).getByRole("button", { name: "Cambiar ahora" }));
    const dialogo = await screen.findByRole("alertdialog", { name: "¿Lo hago ahora?" });
    expect(confirmarAccion).not.toHaveBeenCalled();
    await user.click(within(dialogo).getByRole("button", { name: "Sí, confirmar" }));
    expect(confirmarAccion).toHaveBeenCalledWith("acc_1");
    expect(await screen.findByRole("status")).toHaveTextContent("Confirmada");
  });

  it("falls back to the notice's text without a structured digest", async () => {
    vi.mocked(getRepaso).mockRejectedValue(new Error("404"));
    render(<RepasoCard {...props} />);
    expect(await screen.findByText("Texto del aviso.")).toBeInTheDocument();
  });
});
