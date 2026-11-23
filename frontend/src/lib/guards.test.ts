import { describe, expect, it } from "vitest";

import { isRecord, textoDeValor } from "./guards";

describe("isRecord", () => {
  it("accepts plain objects only", () => {
    expect(isRecord({ a: 1 })).toBe(true);
    expect(isRecord(null)).toBe(false);
    expect(isRecord([])).toBe(false);
    expect(isRecord("x")).toBe(false);
  });
});

describe("textoDeValor", () => {
  it("never gives [object Object]", () => {
    expect(textoDeValor({ a: 1 })).toBe('{"a":1}');
    expect(textoDeValor([1, 2])).toBe("[1,2]");
    expect(textoDeValor(12.5)).toBe("12.5");
    expect(textoDeValor(true)).toBe("true");
  });
});
