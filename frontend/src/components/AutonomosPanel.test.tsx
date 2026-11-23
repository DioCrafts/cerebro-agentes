import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import * as api from "../apiAutonomos";
import { AutonomosPanel } from "./AutonomosPanel";
import { simularMatchMedia } from "./calculos/entornoPruebas";

simularMatchMedia();

const base = { ejercicio: 2026, lineas: [{ clave: null, concepto: "Ingresos", importe: "20000.00", referencia: "art. 28 LIRPF" }], avisos: [], parametros_no_verificados: [], fuente: "Ley 35/2006" };

describe("AutonomosPanel", () => {
  beforeEach(() => {
    vi.spyOn(api, "getEjerciciosAutonomos").mockResolvedValue([2025, 2026]);
  });
  afterEach(() => vi.restoreAllMocks());

  it("computes the activity without a case and hands the net income to the simulator", async () => {
    const user = userEvent.setup();
    const calcular = vi.spyOn(api, "calcularActividad").mockResolvedValue({
      ...base,
      ingresos_integros: "20000.00",
      gastos_deducibles: "5000.00",
      gastos_dificil_justificacion: "750.00",
      reduccion_art_32_1: "0",
      reduccion_art_32_2: "0",
      reduccion_art_32_3: "0",
      rendimiento_actividad_neto: "14250.00",
    } as unknown as api.ResultadoActividad);
    const onUse = vi.fn();
    render(<AutonomosPanel open onUse={onUse} taxPeriod="2026" />);
    await user.type(screen.getByLabelText("Ingresos del año"), "20000");
    await user.type(screen.getByLabelText("Cuota de autónomos"), "5000");

    const resultado = await screen.findByRole("region", { name: "Resultado" }, { timeout: 2000 });
    expect(resultado).toHaveTextContent("Rendimiento neto de tu actividad");
    expect(calcular).toHaveBeenLastCalledWith(expect.objectContaining({ ejercicio: 2026, ingresos_integros: 20000, seguridad_social_autonomo: 5000 }), expect.any(AbortSignal));
    await user.click(screen.getByRole("button", { name: "Usar en mi simulación" }));
    expect(onUse).toHaveBeenCalledWith(14250);
  });

  it("asks for the quarter of the 130 and sends it", async () => {
    const user = userEvent.setup();
    const calcular = vi.spyOn(api, "calcularModelo130").mockResolvedValue({
      ...base,
      trimestre: 2,
      rendimiento_neto_acumulado: "8000.00",
      pago_sobre_rendimiento: "1600.00",
      pago_previo_a_minoracion: "1600.00",
      minoracion_aplicada: "0",
      minoracion_pendiente: "0",
      resultado: "1600.00",
      obligado_a_pago_fraccionado: true,
      motivo_obligacion: "Tu actividad no es profesional.",
      a_ingresar: "1600.00",
    });
    render(<AutonomosPanel open onUse={() => undefined} taxPeriod="2026" />);
    await user.click(screen.getByRole("tab", { name: "Pago trimestral (130)" }));
    expect(screen.getByRole("tabpanel")).toHaveAttribute("aria-labelledby", "autonomos-tab-m130");
    await user.click(screen.getByRole("button", { name: /2\.º trimestre/ }));
    expect(screen.getByText(/hasta el 30 de junio/)).toBeInTheDocument();
    await user.type(screen.getByLabelText("Ingresos acumulados"), "10000");

    expect(await screen.findByText("Tienes que presentarlo.", { exact: false }, { timeout: 2000 })).toBeInTheDocument();
    expect(calcular).toHaveBeenLastCalledWith(expect.objectContaining({ ejercicio: 2026, trimestre: 2, ingresos_acumulados: 10000 }), expect.any(AbortSignal));
  });

  it("says so when the calculators cannot be reached", async () => {
    vi.mocked(api.getEjerciciosAutonomos).mockRejectedValue(new Error("red"));
    render(<AutonomosPanel open onUse={() => undefined} />);
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("No he podido preparar los cálculos de autónomos."));
  });

  it("tries again with «Reintentar» after a failed load", async () => {
    const user = userEvent.setup();
    vi.mocked(api.getEjerciciosAutonomos).mockRejectedValueOnce(new Error("red")).mockResolvedValue([2026]);
    render(<AutonomosPanel open onUse={() => undefined} />);
    await user.click(await screen.findByRole("button", { name: "Reintentar" }));
    await waitFor(() => expect(screen.queryByRole("alert")).not.toBeInTheDocument());
    expect(api.getEjerciciosAutonomos).toHaveBeenCalledTimes(2);
  });
});
