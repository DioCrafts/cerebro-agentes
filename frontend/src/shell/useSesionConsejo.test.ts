import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import * as api from "../api";
import { ApiError } from "../api";
import type * as consejo from "../apiConsejo";
import { cuerpoSse } from "../test/sse";
import { esperaReintento, reintentable, useSesionConsejo } from "./useSesionConsejo";

describe("esperaReintento", () => {
  it("doubles the wait up to a minute", () => {
    expect([0, 1, 2, 3, 4, 10].map(esperaReintento)).toEqual([4000, 8000, 16000, 32000, 60000, 60000]);
  });
});

describe("reintentable", () => {
  it("retries network and server errors, not refusals", () => {
    expect(reintentable(new TypeError("fetch"))).toBe(true);
    expect(reintentable(new ApiError("caído", 503))).toBe(true);
    expect(reintentable(new ApiError("no existe", 404))).toBe(false);
  });
});

const sesion = (id: string, estado: string) => ({ id, estado, progreso: [] }) as unknown as consejo.SesionConsejo;
const evento = (s: consejo.SesionConsejo, id: string) => `id: ${id}\nevent: sesion\ndata: ${JSON.stringify(s)}\n\n`;
const FIN = 'event: fin\ndata: {"motivo":"terminada"}\n\n';

describe("useSesionConsejo", () => {
  beforeEach(() => vi.useFakeTimers({ shouldAdvanceTime: true }));
  afterEach(() => vi.useRealTimers());

  it("follows the session's stream (no polling) until the server says fin", async () => {
    const abrir = vi
      .spyOn(api, "abrirFlujo")
      .mockResolvedValue(cuerpoSse([evento(sesion("s1", "deliberando"), "a"), evento(sesion("s1", "terminada"), "b"), FIN]));
    const { result } = renderHook(() => useSesionConsejo("c 1", "s1"));
    await waitFor(() => expect(result.current.sesion?.estado).toBe("terminada"));
    expect(abrir).toHaveBeenCalledTimes(1);
    expect(abrir.mock.calls[0]![0]).toBe("/cases/c%201/consejo/s1/eventos");
    await act(async () => {
      await vi.advanceTimersByTimeAsync(120_000);
    });
    expect(abrir).toHaveBeenCalledTimes(1);
    expect(result.current.error).toBeNull();
  });

  it("retries a failed connection with a growing wait, resuming from the last event", async () => {
    const abrir = vi
      .spyOn(api, "abrirFlujo")
      .mockResolvedValueOnce(cuerpoSse([evento(sesion("s1", "deliberando"), "a")]))
      .mockRejectedValueOnce(new ApiError("caído", 503))
      .mockResolvedValue(cuerpoSse([evento(sesion("s1", "terminada"), "b"), FIN]));
    const { result } = renderHook(() => useSesionConsejo("c", "s1"));
    await waitFor(() => expect(result.current.error).toBe("caído"));
    expect(result.current.sesion?.estado).toBe("deliberando"); // the last one read stays on screen
    await act(async () => {
      await vi.advanceTimersByTimeAsync(4000);
    });
    await waitFor(() => expect(result.current.sesion?.estado).toBe("terminada"));
    expect(result.current.error).toBeNull();
    expect(abrir.mock.calls.map((c) => c[1])).toEqual([null, "a", "a"]);
  });

  it("does not retry a refusal", async () => {
    const abrir = vi.spyOn(api, "abrirFlujo").mockRejectedValue(new ApiError("No existe esa sesión del consejo.", 404));
    const { result } = renderHook(() => useSesionConsejo("c", "s1"));
    await waitFor(() => expect(result.current.error).toBe("No existe esa sesión del consejo."));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(120_000);
    });
    expect(abrir).toHaveBeenCalledTimes(1);
  });

  it("never shows the previous session under a new id, and closes the old stream", async () => {
    const senales: AbortSignal[] = [];
    vi.spyOn(api, "abrirFlujo").mockImplementation((ruta, _id, signal) => {
      senales.push(signal);
      const id = ruta.includes("/s1/") ? "s1" : "s2";
      return Promise.resolve(cuerpoSse([evento(sesion(id, "deliberando"), id)], { abierto: true, signal }));
    });
    const { result, rerender } = renderHook(({ id }) => useSesionConsejo("c", id), { initialProps: { id: "s1" } });
    await waitFor(() => expect(result.current.sesion?.id).toBe("s1"));
    rerender({ id: "s2" });
    expect(result.current.sesion).toBeNull();
    expect(senales[0]!.aborted).toBe(true);
    await waitFor(() => expect(result.current.sesion?.id).toBe("s2"));
  });

  it("closes the stream on unmount", async () => {
    let senal: AbortSignal | null = null;
    vi.spyOn(api, "abrirFlujo").mockImplementation((_ruta, _id, signal) => {
      senal = signal;
      return Promise.resolve(cuerpoSse([evento(sesion("s1", "en_cola"), "a")], { abierto: true, signal }));
    });
    const { result, unmount } = renderHook(() => useSesionConsejo("c", "s1"));
    await waitFor(() => expect(result.current.sesion?.estado).toBe("en_cola"));
    unmount();
    expect(senal!.aborted).toBe(true);
  });

  it("opens the stream again on recargar (after confirming)", async () => {
    const abrir = vi
      .spyOn(api, "abrirFlujo")
      .mockResolvedValueOnce(cuerpoSse([evento(sesion("s1", "pendiente_confirmacion"), "a"), FIN]))
      .mockResolvedValueOnce(cuerpoSse([evento(sesion("s1", "en_cola"), "b"), FIN]));
    const { result } = renderHook(() => useSesionConsejo("c", "s1"));
    await waitFor(() => expect(result.current.sesion?.estado).toBe("pendiente_confirmacion"));
    act(() => result.current.recargar());
    await waitFor(() => expect(result.current.sesion?.estado).toBe("en_cola"));
    expect(abrir).toHaveBeenCalledTimes(2);
  });
});
