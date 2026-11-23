import { describe, expect, it } from "vitest";

import { enlaceWeb, esConsentimientoGoogle } from "./urls";

describe("enlaceWeb", () => {
  it("keeps http(s) links", () => {
    expect(enlaceWeb("https://www.boe.es/x")).toBe("https://www.boe.es/x");
    expect(enlaceWeb("http://example.org")).toBe("http://example.org/");
  });
  it.each(["javascript:alert(1)", "data:text/html,hi", "JaVaScRiPt:alert(1)", "/relativa", "", null, undefined])("rejects %s", (url) => {
    expect(enlaceWeb(url)).toBeNull();
  });
});

describe("esConsentimientoGoogle", () => {
  it("accepts only https://accounts.google.com", () => {
    expect(esConsentimientoGoogle("https://accounts.google.com/o/oauth2/v2/auth?x=1")).toBe(true);
    expect(esConsentimientoGoogle("http://accounts.google.com/o/oauth2")).toBe(false);
    expect(esConsentimientoGoogle("https://accounts.google.com.evil.example/")).toBe(false);
    expect(esConsentimientoGoogle("https://evil.example/?accounts.google.com")).toBe(false);
    expect(esConsentimientoGoogle("https://user@accounts.google.com/")).toBe(false);
    expect(esConsentimientoGoogle("javascript:alert(1)")).toBe(false);
  });
});
