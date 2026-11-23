import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import * as api from "../apiTareas";
import type { Paso, ResumenTarea, TareaDetalle } from "../apiTareas";
import { TareasPanel } from "./TareasPanel";

const resumen = (extra: Partial<ResumenTarea> = {}): ResumenTarea => ({
  id: "t1",
  ref: "T-1",
  titulo: "Renovar el pasaporte",
  objetivo: "antes de marzo",
  plantilla: "pasaporte",
  estado: "esperando_usuario",
  estado_texto: "",
  pasos_hechos: 1,
  pasos_total: 2,
  esperando: [],
  proxima_revision: null,
  vence: null,
  creada: "2026-11-01",
  ...extra,
});

const paso = (extra: Partial<Paso>): Paso => ({
  id: "p1",
  titulo: "Pedir cita",
  descripcion: "",
  tipo: "confirmacion_usuario",
  depende_de: [],
  opcional: false,
  estado: "hecho",
  espera_actual: null,
  resultado: null,
  intentos: 0,
  error: null,
  terminado: null,
  argumentos: {},
  capacidad: null,
  espera: null,
  herramienta: null,
  iniciado: null,
  modulo: null,
  operacion: null,
  parametros: {},
  pregunta: null,
  siguiente_intento: null,
  ...extra,
});

const detalle = (estado: ResumenTarea["estado"], pasos: Paso[]): TareaDetalle => ({
  tarea: {
    id: "t1",
    case_id: "c1",
    titulo: "Renovar el pasaporte",
    objetivo: "antes de marzo",
    origen: "plantilla",
    plantilla: "pasaporte",
    estado,
    creada: "2026-11-01",
    vence: null,
    proxima_revision: null,
    pasos,
    motivo_cancelacion: null,
    aceptada: "2026-11-01",
    actualizada: "2026-11-02",
    ambito_decision: null,
    parametros: {},
    plantilla_version: 1,
    terminada: null,
    user_id: null,
    version: 1,
    vinculos: { acciones: [], avisos: [], decisiones: [] },
  },
  resumen: resumen({ estado }),
  historial: [{ seq: 1, tarea_id: "t1", momento: "2026-11-01", evento: "aceptada", paso_id: null, detalle: {} }],
  avisos: [],
});

const esperando = paso({
  id: "p2",
  titulo: "Ir a la comisaría",
  estado: "esperando",
  espera_actual: { motivo: "usuario", desde: "2026-11-02", hasta: null, accion_id: null, aviso_clave: null, detalle: "", reintentar: false },
});

beforeEach(() => {
  vi.spyOn(api, "getTareas").mockResolvedValue({ case_id: "c1", tareas: [resumen()] });
  vi.spyOn(api, "getPlantillas").mockResolvedValue({ plantillas: [{ id: "pasaporte", version: 1, titulo: "Renovar el pasaporte", descripcion: "", pasos: [] }] });
});

describe("TareasPanel", () => {
  it("asks for a case before anything else", () => {
    render(<TareasPanel open caseId={null} onAccion={vi.fn()} />);
    expect(screen.getByRole("heading", { name: "Aún no hay expediente" })).toBeInTheDocument();
  });

  it("lists the tasks like issues, with their state", async () => {
    render(<TareasPanel open caseId="c1" onAccion={vi.fn()} />);
    const lista = await screen.findByRole("region", { name: "Tus tareas" });
    expect(within(lista).getByText("Te espera")).toBeInTheDocument();
    expect(within(lista).getByText(/T-1 · 1 de 2 pasos/)).toBeInTheDocument();
  });

  it("opens a task as a run with its steps and marks the waiting step done", async () => {
    const user = userEvent.setup();
    vi.spyOn(api, "getTarea").mockResolvedValue(detalle("esperando_usuario", [paso({}), esperando]));
    const confirmar = vi.spyOn(api, "confirmarPaso").mockResolvedValue(detalle("activa", [paso({}), paso({ id: "p2", titulo: "Ir a la comisaría" })]));
    const onCambio = vi.fn();
    render(<TareasPanel open caseId="c1" onAccion={vi.fn()} onCambio={onCambio} />);
    await user.click(await screen.findByRole("button", { name: "Renovar el pasaporte" }));

    const pasos = await screen.findByRole("region", { name: "Pasos" });
    expect(within(pasos).getByRole("progressbar", { name: "1 de 2 pasos hechos" })).toBeInTheDocument();
    const fila = within(pasos).getByRole("listitem", { name: "Ir a la comisaría" });
    await user.type(within(fila).getByLabelText("Fecha (opcional)"), "2026-12-01");
    await user.click(within(fila).getByRole("button", { name: "Hecho" }));
    expect(confirmar).toHaveBeenCalledWith("t1", "p2", { fecha: "2026-12-01" });
    expect(onCambio).toHaveBeenCalled();
    expect(await screen.findByRole("region", { name: "Historial" })).toHaveTextContent("La aceptaste");
  });

  it("confirms a pending action through the shell's handler, then advances the task", async () => {
    const user = userEvent.setup();
    const accion = paso({
      id: "p3",
      titulo: "Cambiar de tarifa",
      tipo: "accion",
      estado: "esperando",
      espera_actual: { motivo: "accion", desde: "", hasta: null, accion_id: "a9", aviso_clave: null, detalle: "", reintentar: false },
    });
    vi.spyOn(api, "getTarea").mockResolvedValue(detalle("esperando_usuario", [accion]));
    const operar = vi.spyOn(api, "operarTarea").mockResolvedValue(detalle("completada", [paso({ id: "p3" })]));
    const onAccion = vi.fn().mockResolvedValue(undefined);
    render(<TareasPanel open caseId="c1" onAccion={onAccion} />);
    await user.click(await screen.findByRole("button", { name: "Renovar el pasaporte" }));
    await user.click(await screen.findByRole("button", { name: "Confirmar la acción" }));
    expect(onAccion).toHaveBeenCalledWith("a9", true);
    expect(operar).toHaveBeenCalledWith("t1", "avanzar");
  });

  it("cancels with the reason the user gave", async () => {
    const user = userEvent.setup();
    vi.spyOn(api, "getTarea").mockResolvedValue(detalle("activa", [esperando]));
    const cancelar = vi.spyOn(api, "cancelarTarea").mockResolvedValue(detalle("cancelada", [esperando]));
    render(<TareasPanel open caseId="c1" onAccion={vi.fn()} />);
    await user.click(await screen.findByRole("button", { name: "Renovar el pasaporte" }));
    await user.click(await screen.findByRole("button", { name: "Cancelar" }));
    await user.type(screen.getByLabelText("¿Por qué?"), "ya no viajo");
    await user.click(screen.getByRole("button", { name: "Confirmar la cancelación" }));
    expect(cancelar).toHaveBeenCalledWith("t1", "ya no viajo");
  });
});
