import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { Citation, InformeCerebro, ProgressEvent } from "../api";
import { ConsultadosLinea, PlanEnVivo, ProcedenciaRespuesta } from "./ProcedenciaRespuesta";

const cerebro: InformeCerebro = {
  conversacion_id: "conv1",
  intencion: { tipo: "cruzada", encargos: [], necesita_memoria: false, confianza: 1, origen: "reglas", resumen: "", aclaracion: null },
  ruta: "sintesis",
  informes: [
    {
      encargo_id: "e1",
      especialista: "hipoteca",
      nombre: "Hipoteca",
      estado: "ok",
      cifras: [{ id: "c1", especialista: "hipoteca", concepto: "Cuota mensual", valor: "650", literal: "650 €", herramienta: "cuota", fragment_id: "F3", unidad: "EUR/mes", ejercicio: null }],
      fragment_ids: [],
      duracion_ms: 1,
      modo: null,
    },
    { encargo_id: "e2", especialista: "fiscal", nombre: "Impuestos", estado: "error", cifras: [], fragment_ids: [], duracion_ms: 1, modo: null },
  ],
  peticiones: [],
  puerta: {
    violaciones: [],
    atribuciones: [
      { literal: "650 €", valor: "650", cifra_id: "c1", especialista: "hipoteca", fragment_id: "F3", del_usuario: false },
      { literal: "30.000 €", valor: "30000", cifra_id: null, especialista: null, fragment_id: null, del_usuario: true },
    ],
    ignorados: [],
  },
  recuerdos_usados: [],
  revisiones_sintesis: 1,
  duracion_ms: 1,
};

const cita = (extra: Pick<Citation, "title" | "locator" | "fragment_id">): Citation => ({
  authority: null,
  binding: null,
  date: null,
  reference: null,
  section: null,
  snippet: null,
  source_type: null,
  vigencia: null,
  vigencia_resumen: null,
  ...extra,
});

describe("ConsultadosLinea", () => {
  it("names who answered and who could not", () => {
    render(<ConsultadosLinea cerebro={cerebro} />);
    const lista = screen.getByRole("list", { name: "Especialistas consultados" });
    expect(lista).toHaveTextContent("Hipoteca");
    expect(lista).toHaveTextContent("Impuestos (no pudo responder)");
    expect(screen.getByText(/revisé la respuesta una vez/)).toBeInTheDocument();
  });
});

describe("ProcedenciaRespuesta", () => {
  it("lists every figure with its origin, and opens the source", async () => {
    const user = userEvent.setup();
    const onCite = vi.fn();
    render(<ProcedenciaRespuesta cerebro={cerebro} citations={[cita({ title: "Ley Hipotecaria", locator: "x", fragment_id: "F3" })]} onCite={onCite} />);
    await user.click(screen.getByText("¿De dónde sale cada cifra?"));
    const [primera, segunda] = screen.getAllByRole("listitem") as [HTMLElement, HTMLElement];
    expect(primera).toHaveTextContent("650 €");
    expect(primera).toHaveTextContent("Hipoteca");
    expect(primera).toHaveTextContent("cálculo «cuota»");
    expect(segunda).toHaveTextContent("Tú lo dijiste");
    await user.click(within(primera).getByRole("button", { name: "Ver la fuente F3: Ley Hipotecaria" }));
    expect(onCite).toHaveBeenCalledWith("F3");
  });

  it("renders nothing without the brain's report", () => {
    const { container } = render(<ProcedenciaRespuesta cerebro={null} citations={[]} />);
    expect(container).toBeEmptyDOMElement();
  });
});

describe("PlanEnVivo", () => {
  it("shows each specialist's progress", () => {
    const eventos: ProgressEvent[] = [
      {
        type: "plan",
        intencion: {
          ...cerebro.intencion,
          resumen: "Consulto a Hipoteca e Impuestos",
          encargos: [
            { id: "e1", especialista: "hipoteca", depende_de: [] },
            { id: "e2", especialista: "fiscal", depende_de: ["e1"] },
          ],
        },
      } as ProgressEvent,
      { type: "especialista", clave: "hipoteca", nombre: "Hipoteca", encargo_id: "e1", estado: "done", resultado: "ok" },
    ];
    render(<PlanEnVivo eventos={eventos} />);
    const lista = screen.getByRole("list", { name: "Especialistas" });
    const [hipoteca, fiscal] = within(lista).getAllByRole("listitem") as [HTMLElement, HTMLElement];
    expect(hipoteca).toHaveTextContent("Hipotecalisto");
    expect(fiscal).toHaveTextContent("en espera");
    expect(fiscal).toHaveTextContent("(espera a otro informe)");
  });
});
