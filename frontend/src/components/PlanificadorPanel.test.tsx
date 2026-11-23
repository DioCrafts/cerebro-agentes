import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import * as api from "../apiPlanificador";
import { simularMatchMedia } from "./calculos/entornoPruebas";
import { PlanificadorPanel } from "./PlanificadorPanel";

simularMatchMedia();

const plan = {
  ejercicio: 2026,
  fecha: "2026-11-23",
  fecha_horizonte: "2031-11-23",
  horizonte_anos: 5,
  rentabilidad_alternativa_pct: "2.00",
  vista: "diferimiento",
  importe_total: "10000.00",
  asignacion: [{ id: "liquidez", nombre: "Dejar líquido", importe: "4000.00", efectivo: "4000.00", detalle: "Colchón" }],
  valoracion: {
    componentes: [{ concepto: "Ahorro en el IRPF", importe: "450.00", naturaleza: "diferimiento", palanca: "plan", modulo: "fiscal", referencia: "art. 51 LIRPF" }],
    total: "1234.56",
    total_diferimiento: "1234.56",
    total_definitivo: "900.00",
    valor_actual: "1100.00",
    impuesto_ejercicio: "3000.00",
    tributacion: "individual",
  },
  palancas: [],
  no_usadas: [],
  suma_aislados: "0",
  interaccion: "0",
  explicacion_interaccion: [],
  escenarios: [{ nombre: "Plan óptimo", total: "1234.56", factible: true, nota: "" }],
  hipoteca: null,
  ayudas: [],
  supuestos: [],
  avisos: [],
  no_modelado: ["La inflación"],
  citas: [],
  textos_legales: [],
  busqueda: { metodo: "exhaustiva", resolucion: "100.00", resolucion_hipoteca: null, combinaciones: 120, nota: "" },
  explicacion: "",
} as unknown as api.PlanHogar;

describe("PlanificadorPanel", () => {
  afterEach(() => vi.restoreAllMocks());

  it("asks for an amount before calculating and points at the field", async () => {
    const user = userEvent.setup();
    const planificar = vi.spyOn(api, "planificar");
    render(<PlanificadorPanel caseId={null} taxPeriod="2026" />);
    await user.click(screen.getByRole("button", { name: "Calcular el mejor reparto" }));
    expect(screen.getByText("Indica el dinero que puedes destinar ahora o un excedente mensual.")).toBeInTheDocument();
    expect(screen.getByLabelText("Dinero que puedes destinar ahora")).toHaveFocus();
    expect(planificar).not.toHaveBeenCalled();
  });

  it("sends what the user stated, shows the plan in its tab and marks it outdated after a change", async () => {
    const user = userEvent.setup();
    const planificar = vi.spyOn(api, "planificar").mockResolvedValue(plan);
    render(<PlanificadorPanel caseId="c1" taxPeriod="2026" />);
    await user.type(screen.getByLabelText("Dinero que puedes destinar ahora"), "10000");
    await user.click(screen.getByRole("radio", { name: "Sin colchón" }));
    await user.click(screen.getByRole("checkbox", { name: "Donativo" }));
    await user.click(screen.getByRole("button", { name: "Calcular el mejor reparto" }));

    await waitFor(() => expect(screen.getByRole("tab", { name: /Tu plan/ })).toHaveAttribute("aria-selected", "true"));
    expect(planificar).toHaveBeenCalledWith(
      expect.objectContaining({ case_id: "c1", ejercicio: 2026, importe_disponible: 10000, restricciones: { excluidas: ["donativo"], origen: "usuario" } }),
      expect.any(AbortSignal),
    );
    expect(screen.getByRole("heading", { name: /Posición neta en 5 años/ })).toHaveFocus();
    expect(screen.getByRole("region", { name: "Reparto" })).toHaveTextContent("Dejar líquido");
    expect(screen.getByRole("region", { name: "Las cuentas, línea a línea" })).toHaveTextContent("Diferimiento");

    await user.click(screen.getByRole("tab", { name: /Tu dinero/ }));
    await user.type(screen.getByLabelText("Dinero que puedes destinar ahora"), "0");
    expect(screen.getByRole("tab", { name: /Tu plan/ })).toHaveTextContent("desactualizado");
  });

  it("moves between the tabs with the arrow keys", async () => {
    const user = userEvent.setup();
    render(<PlanificadorPanel caseId={null} taxPeriod={null} />);
    screen.getByRole("tab", { name: /Tu dinero/ }).focus();
    await user.keyboard("{ArrowRight}");
    const planTab = screen.getByRole("tab", { name: /Tu plan/ });
    expect(planTab).toHaveFocus();
    expect(planTab).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("heading", { name: "Aún no hay plan" })).toBeInTheDocument();
  });
});
