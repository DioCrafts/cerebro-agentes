import { describe, expect, it } from "vitest";

import type { Fact } from "../../api";
import { gruposDeComunidad, GROUPS, MADRID_GROUP } from "./campos";
import { type ChildRow, childAge, childProblem, childToApi, derivedFromChildren, emptyChild, parseChildrenFact } from "./descendientes";

const row = (patch: Partial<ChildRow>): ChildRow => ({ ...emptyChild("h"), ...patch });
const fact = (value: Fact["value"]): Fact => ({ fact_id: "d", name: "descendientes", value, value_type: "array", source: "user", confidence: 1, updated_at: "" });
let n = 0;
const ids = () => `id-${++n}`;

describe("descendientes", () => {
  it("takes the age from the birth date before the typed age", () => {
    expect(childAge(row({ fecha: "2020-05-01", edad: "9" }), 2026)).toBe(6);
    expect(childAge(row({ edad: "4" }), 2026)).toBe(4);
    expect(childAge(row({}), 2026)).toBeNull();
  });

  it("explains what is wrong with a row", () => {
    expect(childProblem(row({}), 2026)).toMatch(/fecha de nacimiento/);
    expect(childProblem(row({ fecha: "2027-01-01" }), 2026)).toMatch(/después de 2026/);
    expect(childProblem(row({ edad: "5", discapacidad: "120" }), 2026)).toMatch(/discapacidad/);
    expect(childProblem(row({ edad: "5", adopcion: "2010" }), 2026)).toMatch(/adopción/);
    expect(childProblem(row({ edad: "5", gastos: { gastos_guarderia: "-3" } }), 2026)).toMatch(/importe/);
    expect(childProblem(row({ edad: "5" }), 2026)).toBeNull();
  });

  it("sends a row without its defaults", () => {
    expect(childToApi(row({ edad: "2", discapacidad: "40", asistencia: true, gastos: { gastos_guarderia: "1.200,5", gastos_libros_texto: "" } }))).toEqual({
      edad: 2,
      grado_discapacidad: 40,
      necesita_asistencia: true,
      gastos: { gastos_guarderia: 1200.5 },
    });
    // Assistance only counts from 33 % to 64 %.
    expect(childToApi(row({ edad: "2", discapacidad: "70", asistencia: true }))).not.toHaveProperty("necesita_asistencia");
  });

  it("derives the counts only from a complete detail", () => {
    const rows = [row({ edad: "1", gastos: { gastos_guarderia: "100" } }), row({ edad: "10", discapacidad: "65", gastos: { gastos_guarderia: "50,5" } })];
    expect(derivedFromChildren(rows, 2026)).toMatchObject({
      num_descendientes: "2",
      num_descendientes_menores_3: "1",
      num_descendientes_discapacidad: "1",
      num_descendientes_discapacidad_65: "1",
      gastos_guarderia: "150,5",
    });
    expect(derivedFromChildren([row({})], 2026)).toEqual({});
  });

  it("reads the saved fact and flags an unreadable one", () => {
    expect(parseChildrenFact([], ids)).toEqual({ rows: [], broken: false, saved: false });
    expect(parseChildrenFact([fact("{roto")], ids).broken).toBe(true);
    expect(parseChildrenFact([fact("[1]")], ids).broken).toBe(true);
    const { rows } = parseChildrenFact([fact(JSON.stringify([{ edad: 3, gastos: { gastos_guarderia: 99.5 } }]))], ids);
    expect(rows[0]).toMatchObject({ edad: "3", gastos: { gastos_guarderia: "99,5" } });
  });
});

describe("gruposDeComunidad", () => {
  it("shows Madrid's own group, the common ones elsewhere, the state's alone without a region", () => {
    expect(gruposDeComunidad("Madrid")).toContain(MADRID_GROUP);
    expect(gruposDeComunidad("Andalucía").length).toBeGreaterThan(GROUPS.length);
    expect(gruposDeComunidad("")).toBe(GROUPS);
    expect(gruposDeComunidad("Otra")).toBe(GROUPS);
  });
});
