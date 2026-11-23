import { describe, expect, it } from "vitest";

import type { Fact } from "../../api";
import { DATOS_CONYUGE, PREFERENCIAS_OPTIMIZACION } from "../expediente";
import { baseDeExpediente, childrenInCase, estadoDeExpediente, nuevaPosicion } from "./campos";
import { cambiosPendientes, trasGuardar } from "./guardado";
import { type ContextoPeticion, FORMULARIO_VACIO, type FormularioOptimizador, construirPeticion, resolver } from "./peticion";

const fact = (name: string, value: Fact["value"]): Fact => ({ fact_id: name, name, value, value_type: Array.isArray(value) ? "array" : typeof value === "object" ? "object" : typeof value === "number" ? "number" : typeof value === "boolean" ? "boolean" : "string", source: "user", confidence: 1, updated_at: "2026-01-01" });

function run(facts: Fact[], form: Partial<FormularioOptimizador> = {}, ctx: Partial<ContextoPeticion> = {}) {
  const f = { ...FORMULARIO_VACIO, ...form };
  const base = baseDeExpediente(facts);
  const v = resolver(f, base);
  const contexto: ContextoPeticion = { facts, caseId: "c1", taxYear: 2026, today: "2026-11-23", ...ctx };
  const p = construirPeticion(f, base, v, contexto);
  return { base, v, p, g: cambiosPendientes(f, base, v, p) };
}

describe("campos", () => {
  it("reads the civil status loosely", () => {
    expect(estadoDeExpediente(" Casada ")).toBe("casado");
    expect(estadoDeExpediente(3)).toBe("");
  });
  it("counts the per-child detail, or null without one", () => {
    expect(childrenInCase([fact("descendientes", "[{},{}]")])).toBe(2);
    expect(childrenInCase([fact("descendientes", "{roto")])).toBeNull();
    expect(childrenInCase([])).toBeNull();
  });
  it("turns stored positions into rows with stable ids", () => {
    const prefs = JSON.stringify({ perdidas_latentes: [{ importe: "1200.5", tipo: "raro" }] });
    const { base } = run([fact(PREFERENCIAS_OPTIMIZACION, prefs)]);
    expect(base.positionsBaseline).toEqual([
      { id: "guardada-perdida-1", clase: "perdida", descripcion: "", importe: "1200,5", tipo: "valores_cotizados", titular: "contribuyente" },
    ]);
  });
});

describe("construirPeticion", () => {
  it("sends only what differs from the case", () => {
    const { p } = run([fact("rendimiento_trabajo_bruto", 30000)], { edits: { rendimiento_trabajo_bruto: "30.000", retenciones: "4.000,5" } });
    expect(p.body.datos).toEqual({ retenciones: 4000.5 });
    expect(p.body.case_id).toBe("c1");
    expect(p.problems).toEqual({});
  });

  it("sends zero for a cleared figure, but not for a cleared age", () => {
    const { p } = run([fact("donativos", 100), fact("edad", 40)], { edits: { donativos: "", edad: "" } });
    expect(p.datos).toEqual({ donativos: 0 });
  });

  it("marks bad boxes and keeps them out of the request", () => {
    const { p } = run([], { edits: { edad: "200" }, limitEdits: { presupuesto_donativos: "10", donativo_previsto: "50" } });
    expect(Object.keys(p.problems).sort()).toEqual(["opt-edad", "opt-presupuesto_donativos"]);
    expect(p.datos).toEqual({});
    expect(p.restricciones.presupuesto_donativos).toBeNull();
    expect(p.restricciones.donativo_previsto).toBe(50);
  });

  it("sends the spouse in full when married, and null when no figure is given", () => {
    const married = run([fact("estado_civil", "casado")]);
    expect(married.p.body.conyuge).toBeNull();
    const conFiguras = run([fact("estado_civil", "casado")], { spouseEdits: { rendimiento_trabajo_bruto: "20000" } });
    expect(conFiguras.p.body.conyuge).toMatchObject({ rendimiento_trabajo_bruto: 20000, edad: null });
    expect(conFiguras.p.datos.tributacion).toBe("individual");
  });

  it("asks for an amount on an empty latent position", () => {
    const fila = nuevaPosicion("perdida", "x");
    const { p } = run([], { positionsEdit: [fila] });
    expect(p.problems["opt-pos-x-importe"]).toMatch(/importe/);
    expect(p.restricciones.perdidas_latentes).toEqual([]);
  });

  it("keys the plan on its inputs, not on how the figures are written", () => {
    const a = run([], { edits: { retenciones: "1.000" } }).p.key;
    const b = run([], { edits: { retenciones: "1000,00" } }).p.key;
    const c = run([], { edits: { retenciones: "1001" } }).p.key;
    expect(a).toBe(b);
    expect(a).not.toBe(c);
  });
});

describe("cambiosPendientes", () => {
  it("writes the constraints as one fact and counts each change", () => {
    const { g } = run([], { limitEdits: { aportacion_maxima_pensiones: "1500" }, iliquidezEdit: "si" });
    expect(g.cambios).toBe(2);
    expect(g.pendientes).toEqual([[PREFERENCIAS_OPTIMIZACION, JSON.stringify({ aportacion_maxima_pensiones: 1500, acepta_iliquidez: true })]]);
  });

  it("deletes a figure cleared in the form", () => {
    const { g } = run([fact("donativos", 100)], { edits: { donativos: "" } });
    expect(g.pendientes).toEqual([["donativos", null]]);
  });

  it("removes the spouse's figures when every box is emptied", () => {
    const facts = [fact("estado_civil", "casado"), fact(DATOS_CONYUGE, JSON.stringify({ edad: 50 }))];
    const { g } = run(facts, { spouseEdits: { edad: "" } });
    expect(g.pendientes).toEqual([[DATOS_CONYUGE, null]]);
  });

  it("saves nothing while a constraint has a problem", () => {
    const { g } = run([], { limitEdits: { aportacion_maxima_pensiones: "-1" } });
    expect(g.cambios).toBe(0);
  });
});

describe("trasGuardar", () => {
  it("clears what the saved fact held", () => {
    const form: FormularioOptimizador = { ...FORMULARIO_VACIO, edits: { a: "1", b: "2" }, limitEdits: { donativo_previsto: "3" }, iliquidezEdit: "no" };
    expect(trasGuardar(form, "a").edits).toEqual({ b: "2" });
    const tras = trasGuardar(form, PREFERENCIAS_OPTIMIZACION);
    expect(tras.limitEdits).toEqual({});
    expect(tras.iliquidezEdit).toBeNull();
  });
});
