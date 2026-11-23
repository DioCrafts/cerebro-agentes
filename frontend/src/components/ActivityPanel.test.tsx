import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import type { ActivityRow } from "../lib/presentation";
import { ActivitySummary, LiveActivity } from "./ActivityPanel";
import { simularNavegador } from "./conversacion/entornoPruebas";

simularNavegador();

const filas: ActivityRow[] = [
  { kind: "search", text: "deducción por alquiler", outcome: "3 resultados" },
  { kind: "calc", text: "Cálculo del IRPF", outcome: "falló", failed: true },
];

describe("ActivitySummary", () => {
  it("folds the steps behind a summary that counts them", async () => {
    const user = userEvent.setup();
    render(<ActivitySummary rows={filas} elapsedMs={4000} />);
    const resumen = screen.getByText(/Cómo llegué a esta respuesta · 1 búsqueda/);
    expect(screen.getByRole("list", { name: "Cómo llegué a esta respuesta", hidden: true })).not.toBeVisible();
    await user.click(resumen);
    const pasos = screen.getByRole("list", { name: "Cómo llegué a esta respuesta" });
    expect(pasos).toBeVisible();
    expect(pasos).toHaveTextContent("Búsqueda: «deducción por alquiler»");
  });

  it("renders nothing without steps", () => {
    const { container } = render(<ActivitySummary rows={[]} elapsedMs={0} />);
    expect(container).toBeEmptyDOMElement();
  });
});

describe("LiveActivity", () => {
  it("labels the draft as not verified yet", () => {
    render(<LiveActivity events={[{ type: "thinking", step: 1 }]} startedAt={Date.now()} draft="Puedes deducir…" />);
    expect(screen.getByText("Redactando la respuesta…")).toBeInTheDocument();
    expect(screen.getByText("Borrador · aún sin verificar")).toBeInTheDocument();
  });
});
