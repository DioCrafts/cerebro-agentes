import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { Fact } from "../api";
import { OptimizerPanel } from "./OptimizerPanel";

const fact = (name: string, value: Fact["value"]): Fact => ({ fact_id: name, name, value, value_type: "number", source: "user", confidence: 1, updated_at: "" });

function renderPanel(onSaveFact = vi.fn().mockResolvedValue(true)) {
  render(
    <OptimizerPanel
      open
      onClose={() => undefined}
      caseId="c1"
      facts={[fact("rendimiento_trabajo_bruto", 30000)]}
      taxPeriod="2026"
      onSaveFact={onSaveFact}
      onOpenSimulator={() => undefined}
    />,
  );
  return onSaveFact;
}

describe("OptimizerPanel", () => {
  it("counts the changes and saves the constraints as one fact", async () => {
    const user = userEvent.setup();
    const onSaveFact = renderPanel();
    await user.type(screen.getByLabelText("Lo que aún puedes aportar a tu plan"), "1500");
    const guardar = screen.getByRole("button", { name: "Guardar este cambio en mi expediente" });
    await user.click(guardar);
    await waitFor(() => expect(onSaveFact).toHaveBeenCalledWith("preferencias_optimizacion", JSON.stringify({ aportacion_maxima_pensiones: 1500 })));
  });

  it("marks a wrong figure and does not offer to save it", async () => {
    const user = userEvent.setup();
    renderPanel();
    await user.type(screen.getByLabelText("Lo que aún puedes aportar a tu plan"), "-5");
    expect(screen.getByText("No puede ser negativo.")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Hay un dato que revisar");
    expect(screen.queryByRole("button", { name: /Guardar/ })).not.toBeInTheDocument();
  });
});
