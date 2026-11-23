import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import "../test/jsdom";
import * as api from "../apiEvaluacion";
import type { ColaRevision, DetalleCandidato, GrupoResumen } from "../apiEvaluacion";
import { ProveedorUI } from "../ui";
import { RevisionCasosPanel } from "./RevisionCasosPanel";

const grupo: GrupoResumen = {
  id: "g1",
  grupo: "g1",
  pregunta: "¿Cuánto pago de IBI?",
  tipo_fallo: "cifra",
  etiqueta_fallo: "Cifra equivocada",
  categoria_sugerida: "cifras",
  critica: true,
  origen: "voto",
  frecuencia: 3,
  prioridad: 80,
  motivos_prioridad: [],
  importe_en_juego: null,
  creado_el: "2026-11-20",
  estado: "pendiente",
};

const cola = (grupos: GrupoResumen[]): ColaRevision => ({ grupos, operador: false, categorias: [], detectados: 0 });

const detalle = {
  candidato: {
    id: "g1",
    grupo: "g1",
    origen: "voto",
    tipo_fallo: "cifra",
    categoria_sugerida: "cifras",
    critica: true,
    importe_en_juego: null,
    prioridad: 80,
    motivos_prioridad: [],
    estado: "pendiente",
    creado_el: "2026-11-20",
    motivo_rechazo: null,
    caso_aceptado: null,
    contenido: {
      pregunta: "¿Cuánto pago de IBI?",
      respuesta: "Unos 300 €.",
      respuesta_parcial: false,
      siguiente_pregunta: null,
      correccion: {},
      modo: null,
      cerebro: null,
      modos_especialistas: {},
      variante: null,
      modelos: {},
      modelos_origen: "turno",
      senales: [],
      tool_trace: [],
      instantanea: {},
      enmascarado: [],
    },
  },
  etiqueta_fallo: "Cifra equivocada",
  frecuencia: 3,
  duplicados: [],
  categorias: [{ clave: "cifras", descripcion: "", critica: true }],
} as unknown as DetalleCandidato;

function pintar() {
  return render(
    <ProveedorUI>
      <RevisionCasosPanel open caseId="c1" />
    </ProveedorUI>,
  );
}

beforeEach(() => {
  vi.spyOn(api, "getAprendizaje").mockResolvedValue({
    case_id: "c1",
    activo: false,
    version: "v1",
    texto: "Aprender de los fallos de este caso",
    otorgado_el: null,
    candidatos_pendientes: 0,
  });
});

describe("RevisionCasosPanel", () => {
  it("filters the cases by state with a segmented control", async () => {
    const user = userEvent.setup();
    const getCola = vi.spyOn(api, "getColaRevision").mockImplementation((estado) => Promise.resolve(cola(estado === "pendiente" ? [grupo] : [])));
    vi.spyOn(api, "getAceptados").mockResolvedValue({ casos: [] });
    pintar();
    const lista = await screen.findByRole("region", { name: "Casos pendientes" });
    expect(within(lista).getByText("Cifra equivocada")).toBeInTheDocument();
    expect(within(lista).getByText("crítica")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Rechazados" }));
    expect(await screen.findByRole("heading", { name: "Nada por aquí" })).toBeInTheDocument();
    expect(getCola).toHaveBeenLastCalledWith("rechazado", expect.any(AbortSignal));

    await user.click(screen.getByRole("button", { name: "Aceptados" }));
    expect(await screen.findByRole("heading", { name: "Aún no hay casos aceptados" })).toBeInTheDocument();
  });

  it("switches «aprender de los fallos» for the case", async () => {
    const user = userEvent.setup();
    vi.spyOn(api, "getColaRevision").mockResolvedValue(cola([]));
    const cambiar = vi.spyOn(api, "cambiarAprendizaje").mockResolvedValue({
      case_id: "c1",
      activo: true,
      version: "v1",
      texto: "Aprender de los fallos de este caso",
      otorgado_el: "2026-11-23",
      candidatos_pendientes: 0,
    });
    pintar();
    await user.click(await screen.findByRole("button", { name: "Aprender de los fallos de este caso" }));
    expect(cambiar).toHaveBeenCalledWith("c1", true, "v1");
  });

  it("rejects a case only with a reason, and goes back to the list", async () => {
    const user = userEvent.setup();
    vi.spyOn(api, "getColaRevision").mockResolvedValue(cola([grupo]));
    vi.spyOn(api, "getCandidato").mockResolvedValue(detalle);
    const rechazar = vi.spyOn(api, "rechazarCandidato").mockResolvedValue({ rechazados: 3 });
    pintar();
    await user.click(await screen.findByRole("button", { name: "¿Cuánto pago de IBI?" }));
    await user.click(await screen.findByRole("button", { name: "Rechazar" }));
    expect(screen.getByRole("alert")).toHaveTextContent("Indica por qué se rechaza.");
    expect(rechazar).not.toHaveBeenCalled();

    await user.type(screen.getByRole("textbox", { name: "Motivo para rechazar" }), "la cifra era correcta");
    await user.click(screen.getByRole("button", { name: "Rechazar" }));
    expect(rechazar).toHaveBeenCalledWith("g1", "la cifra era correcta");
    expect(await screen.findByRole("status")).toHaveTextContent("Rechazado (3 casos).");
    await waitFor(() => expect(screen.getByRole("region", { name: "Casos pendientes" })).toBeInTheDocument());
  });

  it("asks before deleting an accepted case", async () => {
    const user = userEvent.setup();
    vi.spyOn(api, "getColaRevision").mockResolvedValue(cola([]));
    vi.spyOn(api, "getAceptados").mockResolvedValue({
      casos: [{ id: "a1", categoria: "cifras", descripcion: "IBI de Madrid", expectativas: [{}], avisos: [], aceptado_el: "2026-11-21", vigente: true, motivo_baja: null }],
    });
    const borrar = vi.spyOn(api, "borrarAceptado").mockResolvedValue(null);
    pintar();
    await user.click(screen.getByRole("button", { name: "Aceptados" }));
    await user.click(await screen.findByRole("button", { name: "Borrar" }));
    const dialogo = await screen.findByRole("alertdialog");
    expect(dialogo).toHaveTextContent("Dejará de comprobarse en las evaluaciones.");
    await user.click(within(dialogo).getByRole("button", { name: "Borrar" }));
    await waitFor(() => expect(borrar).toHaveBeenCalledWith("a1"));
  });
});
