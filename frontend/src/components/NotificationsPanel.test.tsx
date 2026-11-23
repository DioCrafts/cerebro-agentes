import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { AdvisorNotification } from "../api";
import { NotificationsPanel } from "./NotificationsPanel";

const aviso = (extra: Partial<AdvisorNotification>): AdvisorNotification =>
  ({
    id: "n1",
    kind: "plazo",
    severity: "normal",
    title: "Presenta el modelo 130",
    body: "Vence el **20 de enero**.",
    amount: null,
    due_date: "2027-01-20",
    actions: [],
    sources: [],
    created_at: new Date().toISOString(),
    read_at: null,
    ...extra,
  }) as AdvisorNotification;

const base = {
  hasCase: true,
  loading: false,
  error: null,
  onMarkRead: () => undefined,
  onRefresh: () => undefined,
  onAsk: () => undefined,
  onOpenPreferences: () => undefined,
};

describe("NotificationsPanel", () => {
  it("explains what will appear before there is a case", () => {
    render(<NotificationsPanel {...base} hasCase={false} notifications={[]} />);
    expect(screen.getByRole("heading", { name: "Aún no hay avisos" })).toBeInTheDocument();
  });

  it("shows an error as a Flash", () => {
    render(<NotificationsPanel {...base} error="No se pudieron cargar" notifications={[]} />);
    expect(screen.getByText("No se pudieron cargar")).toBeInTheDocument();
  });

  it("groups the notices, marks the unread one and lets the user act on it", async () => {
    const user = userEvent.setup();
    const onMarkRead = vi.fn();
    const onAsk = vi.fn();
    render(<NotificationsPanel {...base} notifications={[aviso({})]} onMarkRead={onMarkRead} onAsk={onAsk} />);
    expect(screen.getByRole("region", { name: "Avisos: Impuestos" })).toBeInTheDocument();
    const articulo = screen.getByRole("article", { name: "Presenta el modelo 130" });
    expect(articulo).toHaveTextContent("Nuevo");
    expect(articulo).toHaveTextContent("hasta el");
    await user.click(screen.getByRole("button", { name: "Marcar como leído" }));
    expect(onMarkRead).toHaveBeenCalledWith("n1");
    await user.click(screen.getByRole("button", { name: "Preguntar al asesor" }));
    expect(onAsk).toHaveBeenCalledWith(expect.stringContaining("Presenta el modelo 130"));
  });
});
