import { afterEach, describe, expect, it, vi } from "vitest";

import * as api from "../api";
import { getMe, login, streamAgent } from "../api";
import { cuerpoSse } from "../test/sse";
import { tomarTokenAntiguo } from "./session";
import { borrarConversaciones, emptyConversation, loadConversations, saveConversations } from "./storage";

afterEach(() => {
  localStorage.clear();
  vi.unstubAllGlobals();
});

describe("tomarTokenAntiguo", () => {
  it("takes the token an older version left in localStorage out, once", () => {
    localStorage.setItem("haciendagpt_session_token", "viejo-123");
    expect(tomarTokenAntiguo()).toBe("viejo-123");
    expect(localStorage.getItem("haciendagpt_session_token")).toBeNull();
    expect(tomarTokenAntiguo()).toBeNull();
  });
});

describe("borrarConversaciones", () => {
  it("removes the history with its tax facts from this browser", () => {
    const conversacion = { ...emptyConversation("c1"), facts: [{ name: "edad" }] } as unknown as ReturnType<typeof emptyConversation>;
    saveConversations([{ ...conversacion, turns: [{ id: "t1" } as never] }]);
    expect(loadConversations()).toHaveLength(1);
    borrarConversaciones();
    expect(loadConversations()).toEqual([]);
    expect(localStorage.getItem("haciendagpt_conversations_v1")).toBeNull();
  });
});

function fetchFalso(respuesta: () => Response) {
  const fetch = vi.fn<(url: string, init?: RequestInit) => Promise<Response>>(() => Promise.resolve(respuesta()));
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

describe("the session cookie", () => {
  it("is sent by the browser: no token in storage or headers, credentials same-origin and our CSRF header", async () => {
    const fetch = fetchFalso(() => Response.json({ id: "u1", email: "ana@example.com", created_at: "" }));
    await getMe();
    const init = fetch.mock.calls[0]![1]!;
    expect(init.credentials).toBe("same-origin");
    const headers = init.headers as Record<string, string>;
    expect(headers["X-Requested-With"]).toBe("cerebro");
    expect(headers).not.toHaveProperty("Authorization");
    expect(Object.keys(localStorage)).not.toContain("haciendagpt_session_token");
  });

  it("logs in without receiving the token (the server set the cookie)", async () => {
    fetchFalso(() => Response.json({ token: null, expires_at: "2026-11-24T00:00:00Z", user: { id: "u1", email: "ana@example.com" } }));
    const sesion = await login({ email: "ana@example.com", password: "una-clave-larga-1" });
    expect(sesion.token).toBeNull();
    expect(sesion.user.id).toBe("u1");
    expect(localStorage.length).toBe(0);
  });

  it("tells the app when the server rejects the session", async () => {
    const caducada = vi.fn();
    api.onSessionExpired(caducada);
    fetchFalso(() => new Response('{"detail":"Inicia sesión para continuar."}', { status: 401, headers: { "WWW-Authenticate": "Bearer" } }));
    await expect(getMe()).rejects.toMatchObject({ status: 401 });
    expect(caducada).toHaveBeenCalledTimes(1);
    api.onSessionExpired(null);
  });
});

describe("streamAgent", () => {
  const respuesta = {
    answer: "Dos años.",
    mode: "verified",
    citations: [],
    claims: [],
    warnings: [],
    tool_trace: [],
    obligations: [],
    facts: [],
  };

  it("reads the turn's Server-Sent Events: progress, then the result", async () => {
    const fetch = fetchFalso(
      () =>
        new Response(
          cuerpoSse([
            'id: 1\nevent: cola\ndata: {"maximo":4}\n\n',
            ": ping\n\n",
            'id: 2\nevent: progress\ndata: {"type":"thinking","iteration":0}\n\n',
            `id: 3\nevent: result\ndata: ${JSON.stringify(respuesta)}\n\n`,
          ]),
          { headers: { "Content-Type": "text/event-stream" } },
        ),
    );
    const progreso: unknown[] = [];
    const resultado = await streamAgent({ query: "¿Plazo?" }, (e) => progreso.push(e));
    expect(resultado.answer).toBe("Dos años.");
    expect(progreso).toEqual([{ type: "thinking", iteration: 0 }]);
    const init = fetch.mock.calls[0]![1]!;
    expect(init.credentials).toBe("same-origin");
    expect((init.headers as Record<string, string>).Accept).toBe("text/event-stream");
  });

  it("turns an error event into an ApiError with its status", async () => {
    fetchFalso(() => new Response(cuerpoSse(['id: 1\nevent: error\ndata: {"status":503,"detail":"Hay demasiadas consultas en curso."}\n\n'])));
    await expect(streamAgent({ query: "x" }, () => undefined)).rejects.toMatchObject({
      status: 503,
      message: "Hay demasiadas consultas en curso.",
    });
  });

  it("fails clearly when the stream ends without a result", async () => {
    fetchFalso(() => new Response(cuerpoSse(['id: 1\nevent: progress\ndata: {"type":"thinking"}\n\n'])));
    await expect(streamAgent({ query: "x" }, () => undefined)).rejects.toThrow("La conexión se cerró antes de recibir la respuesta.");
  });
});
