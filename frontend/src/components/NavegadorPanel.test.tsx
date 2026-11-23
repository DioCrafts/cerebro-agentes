import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import "../test/jsdom";
import * as api from "../api";
import * as nav from "../apiNavegador";
import type { EstadoNavegador, TareaInfo } from "../apiNavegador";
import { cuerpoSse } from "../test/sse";
import { NavegadorPanel, TareaEnVivo } from "./NavegadorPanel";

const tarea = (extra: Partial<TareaInfo> = {}): TareaInfo => ({
  id: "n1",
  case_id: "c1",
  guion: "datadis",
  version: 1,
  titulo: "Descargar el consumo de Datadis",
  modo: "ventana",
  estado: "trabajando",
  creada: "",
  actualizada: "1",
  parametros: {},
  eventos: [],
  pasos: [{ id: "s1", descripcion: "Abrir la web", estado: "ok" }],
  tiene_captura: false,
  resumen_envio: [],
  importacion_error: false,
  origenes_bloqueados: [],
  ...extra,
});

const estado = (extra: Partial<EstadoNavegador> = {}): EstadoNavegador => ({
  activo: true,
  modos: ["ventana"],
  consentimiento: true,
  texto_consentimiento: "El agente visitará Datadis por ti.",
  texto_version: "v1",
  tareas: [
    {
      clave: "datadis",
      version: 1,
      titulo: "Descargar el consumo de Datadis",
      descripcion: "Tu consumo eléctrico",
      verificado_contra_sitio_real: false,
      nota_verificacion: "Aún no se ha probado contra la web real.",
      demostracion: false,
      sitios: ["datadis.es"],
      parametros: [{ nombre: "cups", tipo: "cups", etiqueta: "CUPS", obligatorio: true, opciones: [], personal: true }],
      final: "descarga",
    },
  ],
  max_tareas_dia: 5,
  usadas_hoy: 1,
  ...extra,
});

describe("NavegadorPanel", () => {
  it("says when the operator has not switched the agent on", async () => {
    vi.spyOn(nav, "getEstadoNavegador").mockResolvedValue(estado({ activo: false }));
    render(<NavegadorPanel open caseId="c1" />);
    expect(await screen.findByRole("heading", { name: "El agente con navegador está desactivado" })).toBeInTheDocument();
  });

  it("starts a task only after the user reads and accepts what the agent will do", async () => {
    const user = userEvent.setup();
    vi.spyOn(nav, "getEstadoNavegador").mockResolvedValue(estado());
    vi.spyOn(nav, "getTareas").mockResolvedValue([]);
    const crear = vi.spyOn(nav, "crearTarea").mockResolvedValue(tarea({ estado: "descargado" }));
    render(<NavegadorPanel open caseId="c1" />);
    const lista = await screen.findByRole("region", { name: "¿Qué quieres que haga?" });
    expect(within(lista).getByText("Sin probar en el sitio real")).toBeInTheDocument();
    await user.click(within(lista).getByRole("button", { name: /Elegir/ }));

    const formulario = screen.getByRole("region", { name: "Descargar el consumo de Datadis" });
    await user.type(within(formulario).getByRole("textbox", { name: /CUPS/ }), "ES0021");
    const empezar = within(formulario).getByRole("button", { name: "Empezar" });
    expect(empezar).toBeDisabled();
    await user.click(within(formulario).getByRole("checkbox", { name: "Lo he leído y quiero empezar" }));
    await user.click(empezar);
    expect(crear).toHaveBeenCalledWith("c1", "datadis", { cups: "ES0021" }, { modo: "ventana", acepto: true, texto_version: "v1", sesion: null });
    expect(await screen.findByRole("region", { name: "Tarea en curso" })).toBeInTheDocument();
  });
});

describe("TareaEnVivo", () => {
  it("follows a running task over SSE and closes the stream on unmount", async () => {
    let senal: AbortSignal | undefined;
    const abrir = vi.spyOn(api, "abrirFlujo").mockImplementation((_ruta, _ultimo, signal) => {
      senal = signal;
      const datos = tarea({ pasos: [...tarea().pasos, { id: "s2", descripcion: "Descargar el CSV", estado: "en_curso" }] });
      return Promise.resolve(cuerpoSse([`id: 1\nevent: tarea\ndata: ${JSON.stringify(datos)}\n\n`], { abierto: true, signal }));
    });
    const onActualizada = vi.fn();
    const { unmount } = render(<TareaEnVivo tarea={tarea()} onActualizada={onActualizada} onCerrar={vi.fn()} />);

    expect(screen.getByRole("status")).toHaveTextContent("Trabajando");
    expect(screen.getByRole("button", { name: "Detener" })).toBeInTheDocument();
    await waitFor(() => expect(onActualizada).toHaveBeenCalledWith(expect.objectContaining({ id: "n1" })));
    expect(abrir.mock.calls[0]![0]).toBe("/navegador/tareas/n1/eventos");
    expect((onActualizada.mock.calls[0]![0] as TareaInfo).pasos).toHaveLength(2);

    unmount();
    expect(senal?.aborted).toBe(true);
  });

  it("does not open a stream for a task that has stopped, and offers to discard it", async () => {
    const user = userEvent.setup();
    const abrir = vi.spyOn(api, "abrirFlujo");
    const cerrar = vi.spyOn(nav, "cerrarTarea").mockResolvedValue(tarea({ estado: "cerrada" }));
    const onCerrar = vi.fn();
    render(
      <TareaEnVivo
        tarea={tarea({ estado: "detenido_antes_de_enviar", resumen_envio: [{ etiqueta: "Expone", valor: "Solicito…" }] })}
        onActualizada={vi.fn()}
        onCerrar={onCerrar}
      />,
    );
    expect(screen.getByRole("status")).toHaveTextContent("Preparado: detenido antes de enviar");
    expect(screen.getByText("Lo que se enviaría (no se ha enviado)")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Cerrar y descartar" }));
    expect(cerrar).toHaveBeenCalledWith("n1");
    await waitFor(() => expect(onCerrar).toHaveBeenCalled());
    expect(abrir).not.toHaveBeenCalled();
  });
});
