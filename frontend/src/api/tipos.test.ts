// Where the schema says `string` and the UI needs one of a few values, the client narrows at run time
// instead of trusting the cast: unknown values are dropped, not passed on to a `Record<Literal, …>` lookup.

import { afterEach, describe, expect, it, vi } from "vitest";

import { getConsentimientoEvaluacion } from "../apiEvaluacion";
import { getEstadoNavegador } from "../apiNavegador";

function responde(cuerpo: unknown) {
  vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(Response.json(cuerpo))));
}

afterEach(() => vi.unstubAllGlobals());

describe("runtime narrowing of schema strings", () => {
  it("keeps only the kinds of correction this client knows", async () => {
    responde({
      version: "v1",
      texto: "…",
      aprendizaje_version: "a1",
      aprendizaje_texto: "…",
      tipos: [
        { clave: "cifra_erronea", etiqueta: "Una cifra está mal" },
        { clave: "algo_nuevo", etiqueta: "Un tipo que aún no existe aquí" },
        null,
      ],
    });
    const consentimiento = await getConsentimientoEvaluacion();
    expect(consentimiento.tipos).toEqual([{ clave: "cifra_erronea", etiqueta: "Una cifra está mal" }]);
  });

  it("keeps only the browser modes this client can start", async () => {
    responde({
      activo: true,
      modos: ["ventana", "remoto"],
      consentimiento: false,
      texto_consentimiento: "",
      texto_version: "v1",
      tareas: [],
      max_tareas_dia: 3,
      usadas_hoy: 0,
    });
    const estado = await getEstadoNavegador();
    expect(estado.modos).toEqual(["ventana"]);
  });
});
