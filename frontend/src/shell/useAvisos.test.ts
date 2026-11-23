import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import * as api from "../api";
import { ApiError } from "../api";
import type { AdvisorNotification, NotificationsResponse } from "../api";
import { useAvisos } from "./useAvisos";

const aviso = (id: string, read_at: string | null = null) =>
  ({ id, kind: "plazo", severity: "normal", title: id, body: "", actions: [], sources: [], amount: null, due_date: null, created_at: "2026-11-20T08:00:00Z", read_at }) as unknown as AdvisorNotification;

const respuesta = (items: AdvisorNotification[]): NotificationsResponse =>
  ({ case_id: "c1", unread: items.filter((n) => !n.read_at).length, notifications: items });

afterEach(() => vi.restoreAllMocks());

describe("useAvisos: marcar como leído", () => {
  it("marks the notice read on screen at once and keeps what the server answers", async () => {
    vi.spyOn(api, "runProactive").mockResolvedValue(respuesta([aviso("a"), aviso("b")]));
    let responder: (r: NotificationsResponse) => void = () => undefined;
    const marcar = vi.spyOn(api, "markNotificationRead").mockReturnValue(new Promise((r) => (responder = r)));
    const { result } = renderHook(() => useAvisos("c1", []));
    await waitFor(() => expect(result.current.sinLeer).toBe(2));

    act(() => result.current.marcarLeido("a"));
    // Before the server answers: «a» is already read, the badge counts one.
    expect(marcar).toHaveBeenCalledWith({ caseId: "c1", notificationId: "a" });
    expect(result.current.sinLeer).toBe(1);
    expect(result.current.items.find((n) => n.id === "a")?.read_at).not.toBeNull();

    await act(() => {
      responder(respuesta([aviso("a", "2026-11-23T10:00:00Z"), aviso("b")]));
      return Promise.resolve();
    });
    expect(result.current.items.find((n) => n.id === "a")?.read_at).toBe("2026-11-23T10:00:00Z");
    expect(result.current.error).toBeNull();
  });

  it("puts the notice back as unread and says so when the server cannot record it", async () => {
    vi.spyOn(api, "runProactive").mockResolvedValue(respuesta([aviso("a")]));
    vi.spyOn(api, "markNotificationRead").mockRejectedValue(new ApiError("Servicio caído", 503));
    const { result } = renderHook(() => useAvisos("c1", []));
    await waitFor(() => expect(result.current.sinLeer).toBe(1));

    act(() => result.current.marcarLeido("a"));
    await waitFor(() => expect(result.current.error).toMatch(/No se pudo marcar como leído/));
    expect(result.current.sinLeer).toBe(1);
  });
});
