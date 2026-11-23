import { describe, expect, it } from "vitest";

import { REGISTRO } from "./registro";
import { GRUPOS } from "./tipos";

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
});
