// Readable access to the types generated from the backend's OpenAPI schema (`esquema.d.ts`, made by
// `npm run tipos` from `openapi.json`). The api*.ts clients alias these instead of copying the Pydantic models
// by hand; `make tipos-check` fails when the schema and the generated file drift apart.

import type { components, paths } from "./esquema";

/** Every named model in the schema (`Esquemas["AgentResponse"]`). */
export type Esquemas = components["schemas"];

type Metodo = "get" | "post" | "put" | "patch" | "delete";

type CuerpoJson<R> = R extends { content: { "application/json": infer T } } ? T : never;

/**
 * The JSON body of an endpoint's success answer (200 or 201), by path and method:
 * `Respuesta<"/simulate", "post">`. Lets a client name a model whose schema name collides with another one
 * (FastAPI then prefixes it with its module) without spelling that prefix.
 */
export type Respuesta<P extends keyof paths, M extends Metodo & keyof paths[P]> = paths[P][M] extends {
  responses: infer R;
}
  ? R extends { 200: infer C }
    ? CuerpoJson<C>
    : R extends { 201: infer C }
      ? CuerpoJson<C>
      : never
  : never;

/** The JSON body an endpoint accepts, by path and method. */
export type Peticion<P extends keyof paths, M extends Metodo & keyof paths[P]> = paths[P][M] extends {
  requestBody?: infer B;
}
  ? CuerpoJson<NonNullable<B>>
  : never;
