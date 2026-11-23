import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "../api";
import { reintentable } from "../shell/useFlujoSse";
import { cuerpoSse } from "../test/sse";
import { type EventoSse, LectorSse, esperaReintento, seguirFlujo } from "./sse";

describe("LectorSse", () => {
  it("joins events split across chunks and skips comments", () => {
    const lector = new LectorSse();
    expect(lector.leer(": ping\n\nid: 1\nevent: progreso\nda")).toEqual([]);
    expect(lector.leer('ta: {"a":1}\r\n\r\nevent: fin\ndata: {}\n\n')).toEqual([
      { event: "progreso", data: '{"a":1}', id: "1" },
      // The last id is kept for the events that do not repeat it, as the spec says.
      { event: "fin", data: "{}", id: "1" },
    ]);
    expect(lector.ultimoId).toBe("1");
  });

  it("joins multi-line data and defaults the name to message", () => {
    expect(new LectorSse().leer("data: uno\ndata: dos\n\n")).toEqual([{ event: "message", data: "uno\ndos", id: null }]);
  });
});

describe("esperaReintento", () => {
  it("doubles the wait up to a minute", () => {
    expect([0, 1, 2, 3, 4, 10].map(esperaReintento)).toEqual([4000, 8000, 16000, 32000, 60000, 60000]);
  });
});

describe("seguirFlujo", () => {
  beforeEach(() => vi.useFakeTimers({ shouldAdvanceTime: true }));
  afterEach(() => vi.useRealTimers());

  it("reconnects after a failure with Last-Event-ID and a growing wait, and stops at fin", async () => {
    const vistos: EventoSse[] = [];
    const errores: Array<[unknown, boolean]> = [];
    const momentos: number[] = [];
    const respuestas = [
      () => Promise.resolve(cuerpoSse(["id: a\nevent: x\ndata: 1\n\n"])),
      () => Promise.reject(new TypeError("Failed to fetch")),
      () => Promise.resolve(cuerpoSse(["id: b\nevent: x\ndata: 2\n\nevent: fin\ndata: {}\n\n"])),
    ];
    const abrir = vi.fn<(id: string | null, signal: AbortSignal) => Promise<ReadableStream<Uint8Array>>>(() => {
      momentos.push(Date.now());
      return respuestas[momentos.length - 1]!();
    });
    seguirFlujo({
      abrir,
      alEvento: (e) => {
        vistos.push(e);
        return e.event === "fin" ? "fin" : "seguir";
      },
      alError: (err, reintentara) => errores.push([err, reintentara]),
      reintentable,
    });
    await vi.advanceTimersByTimeAsync(5000);
    await vi.waitFor(() => expect(vistos.map((e) => e.event)).toEqual(["x", "x", "fin"]));
    // The first stream ended without fin (the server's maximum duration): reopened soon, with its last id.
    expect(abrir.mock.calls.map((c) => c[0])).toEqual([null, "a", "a"]);
    expect(momentos[1]! - momentos[0]!).toBeLessThan(4000);
    // The failed attempt was reported as one to retry, and the next waited 4 s.
    expect(errores).toHaveLength(1);
    expect(errores[0]![1]).toBe(true);
    expect(momentos[2]! - momentos[1]!).toBeGreaterThanOrEqual(4000);
    await vi.advanceTimersByTimeAsync(120_000);
    expect(abrir).toHaveBeenCalledTimes(3);
  });

  it("does not retry a refusal", async () => {
    const abrir = vi.fn().mockRejectedValue(new ApiError("No existe", 404));
    const alError = vi.fn();
    seguirFlujo({ abrir, alEvento: () => "seguir", alError, reintentable });
    await vi.waitFor(() => expect(alError).toHaveBeenCalledWith(expect.any(ApiError), false));
    await vi.advanceTimersByTimeAsync(120_000);
    expect(abrir).toHaveBeenCalledTimes(1);
  });

  it("closes the open connection when stopped and never reopens it", async () => {
    let senal: AbortSignal | null = null;
    const abrir = vi.fn((_id: string | null, signal: AbortSignal) => {
      senal = signal;
      return Promise.resolve(cuerpoSse(["event: x\ndata: 1\n\n"], { abierto: true, signal }));
    });
    const alEvento = vi.fn(() => "seguir" as const);
    const alError = vi.fn();
    const parar = seguirFlujo({ abrir, alEvento, alError, reintentable });
    await vi.waitFor(() => expect(alEvento).toHaveBeenCalledTimes(1));
    parar();
    expect(senal!.aborted).toBe(true);
    await vi.advanceTimersByTimeAsync(120_000);
    expect(abrir).toHaveBeenCalledTimes(1);
    expect(alError).not.toHaveBeenCalled();
  });

  it("takes a stream with no bytes for too long as dead and reopens it", async () => {
    const abrir = vi.fn((_id: string | null, signal: AbortSignal) => Promise.resolve(cuerpoSse([], { abierto: true, signal })));
    const alError = vi.fn();
    const parar = seguirFlujo({ abrir, alEvento: () => "seguir", alError, reintentable, silencioMaximoMs: 1000 });
    await vi.advanceTimersByTimeAsync(1100);
    await vi.waitFor(() => expect(alError).toHaveBeenCalledWith(expect.any(Error), true));
    await vi.advanceTimersByTimeAsync(4100);
    await vi.waitFor(() => expect(abrir).toHaveBeenCalledTimes(2));
    parar();
  });
});
