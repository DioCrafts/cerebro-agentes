import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import * as api from "../api";
import { simularMatchMedia } from "./calculos/entornoPruebas";
import { SimulatorPanel } from "./SimulatorPanel";

simularMatchMedia();

const fact = (name: string, value: api.Fact["value"]): api.Fact => ({ fact_id: name, name, value, value_type: "number", source: "user", confidence: 1, updated_at: "" });

describe("SimulatorPanel", () => {
  beforeEach(() => {
    vi.spyOn(api, "simulate").mockReturnValue(new Promise(() => undefined));
  });

  it("says so when a figure could not be saved, and keeps it on screen", async () => {
    const onSaveFact = vi.fn().mockResolvedValue(false);
    const user = userEvent.setup();
    render(
      <SimulatorPanel open caseId="c1" facts={[fact("rendimiento_trabajo_bruto", 30000)]} taxPeriod="2026" onSaveFact={onSaveFact} busy={false} />,
    );
    const salario = screen.getByLabelText(/^Salario bruto/);
    await user.clear(salario);
    await user.type(salario, "32000");
    await user.click(screen.getByRole("button", { name: /Guardar en mi expediente/ }));
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("No se pudo guardar"));
    expect(onSaveFact).toHaveBeenCalledWith("rendimiento_trabajo_bruto", 32000);
    expect(screen.getByLabelText(/^Salario bruto/)).toHaveValue("32000");
  });
});
