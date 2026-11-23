import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { ObligacionDeclarar } from "../lib/obligacion";
import { ObligacionDeclararCard } from "./ObligacionDeclararCard";

const obligacion: ObligacionDeclarar = {
  ejercicio: 2026,
  obligado: "no_se_sabe",
  respuesta: "Depende de cuántos pagadores hayas tenido.",
  regla: { referencia: "art. 96.3 LIRPF", cita: "No tendrán que declarar…", boe_id: "BOE-A-2006-20764" } as ObligacionDeclarar["regla"],
  otras_reglas: [],
  datos_que_faltan: [
    { campo: "num_pagadores_trabajo", pregunta: "¿Cuántas empresas te pagaron este año?", tipo: "numero" },
    { campo: null, pregunta: "¿Cobraste algún premio?", tipo: "si_no" },
  ],
  recomendacion: null,
  resultado_si_declara: null,
  devolucion_estimada: null,
  avisos: [],
};

describe("ObligacionDeclararCard", () => {
  it("shows the verdict, asks for what is missing and passes the answer on", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<ObligacionDeclararCard obligacion={obligacion} value={() => ""} onChange={onChange} dirty={() => false} />);
    const card = screen.getByRole("region", { name: "¿Estás obligado a declarar?" });
    expect(card).toHaveTextContent("Falta un dato");
    expect(card).toHaveTextContent("¿Cobraste algún premio?");
    const pagadores = screen.getByLabelText("Pagadores de tus rendimientos del trabajo");
    expect(pagadores).toHaveAccessibleDescription(/¿Cuántas empresas te pagaron este año\?/);
    await user.type(pagadores, "2");
    expect(onChange).toHaveBeenCalledWith("num_pagadores_trabajo", "2");
    expect(screen.getByText("Qué dice la ley · art. 96.3 LIRPF")).toBeInTheDocument();
  });
});
