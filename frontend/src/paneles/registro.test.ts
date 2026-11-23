import { describe, expect, it, vi } from "vitest";

import { REGISTRO, precargarPanel } from "./registro";
import { GRUPOS, type DefinicionPanel } from "./tipos";

describe("registro de paneles", () => {
  it("cada panel tiene una sección de «Más» conocida y una etiqueta única", () => {
    const grupos = new Set(GRUPOS.map((g) => g.id));
    for (const p of REGISTRO) expect(grupos.has(p.grupo)).toBe(true);
    expect(new Set(REGISTRO.map((p) => p.etiqueta)).size).toBe(REGISTRO.length);
  });

  it("el título empieza por la etiqueta visible y es corto (WCAG 2.5.3)", () => {
    for (const p of REGISTRO) {
      expect(p.titulo.startsWith(p.etiqueta.split(" ")[0] ?? "")).toBe(true);
      expect(p.titulo.length).toBeLessThanOrEqual(50);
    }
  });

  it("las insignias dicen qué cuentan sin repetir la etiqueta", () => {
    const insignias = { avisosSinLeer: 3, accionesPendientes: 1, datosEnExpediente: 4 };
    for (const p of REGISTRO) {
      const insignia = p.insignia?.(insignias);
      if (insignia) expect(insignia.etiqueta.startsWith(p.etiqueta)).toBe(false);
    }
  });

  it("precarga el chunk de un panel una vez, y lo reintenta si la descarga falla", async () => {
    const base = REGISTRO[0] as DefinicionPanel;
    const cargar = vi.fn<DefinicionPanel["cargar"]>().mockRejectedValueOnce(new Error("sin red"));
    const panel: DefinicionPanel = { ...base, id: "prueba-precarga", cargar };
    precargarPanel(panel);
    precargarPanel(panel);
    expect(cargar).toHaveBeenCalledTimes(1);
    await vi.waitFor(() => {
      precargarPanel(panel);
      expect(cargar).toHaveBeenCalledTimes(2);
    });
    precargarPanel(panel);
    expect(cargar).toHaveBeenCalledTimes(2);
  });
});
