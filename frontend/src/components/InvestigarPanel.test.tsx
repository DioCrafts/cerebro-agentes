import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import "../test/jsdom";
import * as api from "../apiInvestigacion";
import type { EstadoInvestigacion, Informe } from "../apiInvestigacion";
import { InvestigarPanel } from "./InvestigarPanel";

const estado = (extra: Partial<EstadoInvestigacion> = {}): EstadoInvestigacion => ({
  fuentes: { corpus: true, boe: true, web: true },
  web_activada_por_el_servicio: true,
  web_consentida: false,
  web_proveedor_compatible: true,
  web_motivo: null,
  dominios_permitidos: [],
  dominios_bloqueados: [],
  limites: { max_usd: "0.50", max_tokens: 1000, llamadas_por_profundidad: {}, precio_busqueda_web_usd: "0.01", precio_busqueda_web_fuente: "" },
  privacidad: "La pregunta se envía al proveedor del modelo.",
  version_texto_consentimiento: "v1",
  ...extra,
});

const informe: Informe = {
  pregunta: "¿Qué necesito para el pasaporte?",
  ambito: null,
  profundidad: "normal",
  fuentes_pedidas: ["corpus", "web"],
  respuesta: "",
  afirmaciones: [{ texto: "Hace falta el DNI en vigor.", tema: "", citas: [{ fuente: "F1", cita: "DNI en vigor" }], oficial: true }],
  fuentes: [
    {
      id: "F1",
      tipo: "web",
      titulo: "Expedición del pasaporte",
      url: "https://www.interior.gob.es/pasaporte",
      referencia: null,
      dominio: null,
      oficial: true,
      recuperada_el: "2026-11-20",
      citada: true,
      sospechosa: false,
      nota: null,
    },
    {
      id: "F2",
      tipo: "web",
      titulo: "Foro de viajes",
      url: "javascript:alert(1)",
      referencia: null,
      dominio: "foro.example",
      oficial: false,
      recuperada_el: "2026-11-20",
      citada: false,
      sospechosa: true,
      nota: null,
    },
  ],
  conflictos: [],
  confianza: "media",
  no_verificado: [],
  avisos: [],
  web: { pedida: true, usada: true, motivo: null, solo_oficiales: false, busquedas: 1, paginas_leidas: 2, vistas_sin_leer: [] },
  coste: { llamadas_modelo: 2, tokens_entrada: 100, tokens_salida: 50, busquedas_web: 1, usd: "0.02", limite_alcanzado: null },
  generado_el: "",
  desde_cache: false,
};

describe("InvestigarPanel", () => {
  it("asks for consent before the web is a source", async () => {
    const user = userEvent.setup();
    vi.spyOn(api, "getEstadoInvestigacion").mockResolvedValue(estado());
    const consentir = vi.spyOn(api, "cambiarConsentimientoWeb").mockResolvedValue(estado({ web_consentida: true }));
    render(<InvestigarPanel open caseId="c1" />);
    await user.click(await screen.findByRole("button", { name: "Permitir la búsqueda web" }));
    expect(consentir).toHaveBeenCalledWith(true);
    expect(await screen.findByRole("checkbox", { name: "Web (buscador del proveedor)" })).toBeEnabled();
  });

  it("sends the question with the chosen depth and lists the sources as links with their domain", async () => {
    const user = userEvent.setup();
    vi.spyOn(api, "getEstadoInvestigacion").mockResolvedValue(estado({ web_consentida: true }));
    const investigar = vi.spyOn(api, "investigar").mockResolvedValue(informe);
    render(<InvestigarPanel open caseId="c1" />);
    await user.type(screen.getByRole("textbox", { name: "Pregunta" }), "¿Qué necesito para el pasaporte?");
    await user.click(screen.getByRole("button", { name: "A fondo" }));
    await user.click(await screen.findByRole("checkbox", { name: "Web (buscador del proveedor)" }));
    await user.click(screen.getByRole("button", { name: "Investigar" }));
    expect(investigar).toHaveBeenCalledWith(
      { pregunta: "¿Qué necesito para el pasaporte?", fuentes: ["corpus", "boe", "web"], profundidad: "a_fondo", solo_oficiales: true, case_id: "c1" },
      expect.any(AbortSignal),
    );

    expect(await screen.findByText("Confianza media")).toBeInTheDocument();
    const fuentes = screen.getByRole("region", { name: "Fuentes" });
    const enlace = within(fuentes).getByRole("link", { name: /Expedición del pasaporte/ });
    expect(enlace).toHaveAttribute("href", "https://www.interior.gob.es/pasaporte");
    expect(within(fuentes).getByText("interior.gob.es")).toBeInTheDocument();
    // A javascript: address never becomes a link; the server's domain is shown instead.
    expect(within(fuentes).queryByRole("link", { name: /Foro de viajes/ })).toBeNull();
    expect(within(fuentes).getByText("foro.example")).toBeInTheDocument();
    expect(within(fuentes).getByText("instrucciones incrustadas ignoradas")).toBeInTheDocument();
  });
});
