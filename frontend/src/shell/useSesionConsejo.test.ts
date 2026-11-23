import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "../api";
import * as consejo from "../apiConsejo";
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

describe("useSesionConsejo", () => {
  beforeEach(() => vi.useFakeTimers({ shouldAdvanceTime: true }));
  afterEach(() => vi.useRealTimers());

  const sesion = (id: string) => ({ id, estado: "terminada" }) as unknown as consejo.SesionConsejo;

  it("retries a failed load and then shows the session", async () => {
    const obtener = vi.spyOn(consejo, "obtenerSesion").mockRejectedValueOnce(new ApiError("caído", 503)).mockResolvedValue(sesion("s1"));
    const { result } = renderHook(() => useSesionConsejo("c", "s1"));
    await waitFor(() => expect(result.current.error).not.toBeNull());
    await act(async () => {
      await vi.advanceTimersByTimeAsync(4000);
    });
    await waitFor(() => expect(result.current.sesion?.id).toBe("s1"));
    expect(result.current.error).toBeNull();
    expect(obtener).toHaveBeenCalledTimes(2);
  });

  it("never shows the previous session under a new id", async () => {
    let resolver: (s: consejo.SesionConsejo) => void = () => {};
    vi.spyOn(consejo, "obtenerSesion")
      .mockResolvedValueOnce(sesion("s1"))
      .mockImplementationOnce(() => new Promise((r) => (resolver = r)));
    const { result, rerender } = renderHook(({ id }) => useSesionConsejo("c", id), { initialProps: { id: "s1" } });
    await waitFor(() => expect(result.current.sesion?.id).toBe("s1"));
    rerender({ id: "s2" });
    expect(result.current.sesion).toBeNull();
    await act(async () => {
      resolver(sesion("s2"));
      await Promise.resolve();
    });
    expect(result.current.sesion?.id).toBe("s2");
  });
});
