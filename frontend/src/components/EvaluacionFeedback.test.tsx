import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { enviarFeedback, getConsentimientoEvaluacion } from "../apiEvaluacion";
import { EvaluacionFeedback } from "./EvaluacionFeedback";
import { simularNavegador } from "./conversacion/entornoPruebas";

simularNavegador();

vi.mock("../apiEvaluacion", () => ({
  enviarFeedback: vi.fn(),
  getConsentimientoEvaluacion: vi.fn(),
  retirarFeedback: vi.fn(),
}));

const respuesta = { id: "f1", guardado_para_evaluacion: false, estado: "ok", etiquetas: [], mensaje: "Gracias por tu valoración." };

describe("EvaluacionFeedback", () => {
  beforeEach(() => {
    vi.mocked(enviarFeedback).mockReset().mockResolvedValue(respuesta);
    vi.mocked(getConsentimientoEvaluacion)
      .mockReset()
      .mockResolvedValue({ version: "v1", texto: "Guardar esta conversación para mejorar el asesor", tipos: [{ clave: "cifra_erronea", etiqueta: "Cifra equivocada" }] });
  });

  it("records a thumbs up", async () => {
    const user = userEvent.setup();
    render(<EvaluacionFeedback caseId="c1" question="¿Cuánto pago?" answer="Unos 300 €." />);
    await user.click(screen.getByRole("button", { name: "Sí, útil" }));
    expect(enviarFeedback).toHaveBeenCalledWith(expect.objectContaining({ valoracion: "positiva", caseId: "c1" }));
    expect(await screen.findByRole("status")).toHaveTextContent("Gracias por tu valoración.");
  });

  it("sends a correction with the consent the user ticked", async () => {
    const user = userEvent.setup();
    render(<EvaluacionFeedback caseId="c1" question="¿Cuánto pago?" answer="Unos 300 €." />);
    await user.click(screen.getByRole("button", { name: "Corregir" }));
    const formulario = screen.getByRole("form", { name: "Corregir la respuesta" });
    expect(formulario).toBeInTheDocument();
    await user.click(await screen.findByRole("radio", { name: "Cifra equivocada" }));
    await user.type(screen.getByRole("textbox", { name: "Cifra correcta (opcional)" }), "250 €");
    await user.click(await screen.findByRole("checkbox", { name: "Guardar esta conversación para mejorar el asesor" }));
    await user.click(screen.getByRole("button", { name: "Enviar" }));
    expect(enviarFeedback).toHaveBeenCalledWith(
      expect.objectContaining({ valoracion: "negativa", tipo: "cifra_erronea", cifraCorrecta: "250 €", versionConsentimiento: "v1" }),
    );
  });
});
