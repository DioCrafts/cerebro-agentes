import { describe, expect, it } from "vitest";

import { amountText, fieldProblem, parseAmount, stated } from "./formulario";

describe("parseAmount", () => {
  it.each([
    ["1.500,50", 1500.5],
    ["1500.5", 1500.5],
    ["1.500 €", 1500],
    ["-200", -200],
    ["1.234.567", 1234567],
  ])("reads %s", (raw, expected) => expect(parseAmount(raw)).toBe(expected));

  it.each(["", "  ", "-", "abc", undefined])("gives null for %s", (raw) => expect(parseAmount(raw)).toBeNull());
});

describe("amountText", () => {
  it("rounds to cents and uses the decimal comma", () => {
    expect(amountText(1500.505)).toBe("1500,51");
    expect(amountText(12)).toBe("12");
  });
});

describe("fieldProblem", () => {
  it("accepts empty boxes and flags", () => {
    expect(fieldProblem("money", "")).toBeNull();
    expect(fieldProblem("flag", "lo que sea")).toBeNull();
  });
  it("rejects what each kind cannot hold", () => {
    expect(fieldProblem("money", "-1")).toMatch(/negativo/);
    expect(fieldProblem("signed", "-1")).toBeNull();
    expect(fieldProblem("count", "2,5")).toMatch(/entero/);
    expect(fieldProblem("age", "131")).toMatch(/edad/);
    expect(fieldProblem("percent", "101")).toMatch(/porcentaje/);
    expect(fieldProblem("money", "doce")).toMatch(/cifra/);
  });
});

describe("stated", () => {
  it("treats equal figures written differently as the same", () => {
    expect(stated("money", "1.500")).toBe(stated("money", "1500,00"));
    expect(stated("flag", "false")).toBe("");
    expect(stated("flag", "true")).toBe("true");
  });
});
