import { act, renderHook } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { crearAlmacenTurno, useTurnoEnCurso } from "./turnoEnCurso";

describe("almacén del turno en curso", () => {
  it("notifies subscribers only on a real change", () => {
    const almacen = crearAlmacenTurno();
    const oyente = vi.fn();
    const baja = almacen.subscribe(oyente);
    almacen.set({ text: "hola", events: [], startedAt: 0, draft: "" });
    almacen.set((p) => p);
    expect(oyente).toHaveBeenCalledTimes(1);
    baja();
    almacen.set(null);
    expect(oyente).toHaveBeenCalledTimes(1);
  });

  it("re-renders the subscribed component as tokens arrive", () => {
    const almacen = crearAlmacenTurno();
    const { result } = renderHook(() => useTurnoEnCurso(almacen));
    expect(result.current).toBeNull();
    act(() => almacen.set({ text: "q", events: [], startedAt: 0, draft: "" }));
    act(() => almacen.set((p) => (p ? { ...p, draft: p.draft + "uno" } : p)));
    expect(result.current?.draft).toBe("uno");
  });
});
